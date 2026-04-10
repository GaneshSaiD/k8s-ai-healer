# approvals/approval_handler.py
# Manages the human-in-the-loop approval gate
# Stores pending approvals, processes approve/reject decisions

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from webhook.models import AlertContext, RemediationPlan

logger = logging.getLogger(__name__)


class ApprovalHandler:
    """
    Manages pending remediation approvals.
    In-memory store for now — Phase 6 will persist to DB.
    """

    def __init__(self):
        # approval_id → {ctx, plan, status, created_at, resolved_at, resolved_by}
        self._pending: dict[str, dict] = {}

    def create_approval(
        self,
        ctx: AlertContext,
        plan: RemediationPlan,
    ) -> str:
        """
        Register a new approval request.
        Returns a unique approval_id.
        """
        approval_id = self._generate_id(ctx, plan)

        self._pending[approval_id] = {
            "approval_id": approval_id,
            "status":      "pending",
            "ctx":         ctx.dict(),
            "plan":        plan.dict(),
            "created_at":  datetime.now(timezone.utc).isoformat(),
            "resolved_at": None,
            "resolved_by": None,
            "result":      None,
        }

        logger.info(
            f"Approval created: {approval_id} | "
            f"action={plan.action} | target={plan.target}"
        )
        return approval_id

    async def approve(
        self,
        approval_id: str,
        approved_by: str = "human",
    ) -> Optional[dict]:
        """
        Approve a pending remediation plan and execute it.
        Returns execution result or None if approval not found.
        """
        entry = self._pending.get(approval_id)
        if not entry:
            logger.warning(f"Approval not found: {approval_id}")
            return None

        if entry["status"] != "pending":
            logger.warning(
                f"Approval {approval_id} already {entry['status']}"
            )
            return entry

        # Mark approved
        entry["status"]      = "approved"
        entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
        entry["resolved_by"] = approved_by

        logger.info(f"Approval {approval_id} approved by {approved_by}")

        # Execute the action
        from webhook.models import RemediationPlan as RP
        from executor.actions import k8s_actions

        plan = RP(**entry["plan"])
        plan.approved = True

        result = await k8s_actions.execute(plan)
        entry["result"] = result.to_dict()

        # Send resolution notification to Slack
        from approvals.slack_notifier import slack_notifier
        from webhook.models import AlertContext as AC

        ctx = AC(**entry["ctx"])
        await slack_notifier.send_resolution(ctx, plan, result.to_dict(), approved_by)

        logger.info(
            f"Action executed: {plan.action} on {plan.target} | "
            f"success={result.success}"
        )

        return entry

    async def reject(
        self,
        approval_id: str,
        rejected_by: str = "human",
        reason: str = "Rejected by operator",
    ) -> Optional[dict]:
        """Reject a pending remediation plan — no action taken."""
        entry = self._pending.get(approval_id)
        if not entry:
            logger.warning(f"Approval not found: {approval_id}")
            return None

        if entry["status"] != "pending":
            logger.warning(
                f"Approval {approval_id} already {entry['status']}"
            )
            return entry

        entry["status"]      = "rejected"
        entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
        entry["resolved_by"] = rejected_by
        entry["result"]      = {"message": reason, "success": False}

        logger.info(f"Approval {approval_id} rejected by {rejected_by}: {reason}")

        # Notify Slack of rejection
        from approvals.slack_notifier import slack_notifier
        from webhook.models import AlertContext as AC, RemediationPlan as RP

        ctx  = AC(**entry["ctx"])
        plan = RP(**entry["plan"])
        await slack_notifier.send_resolution(
            ctx, plan,
            {"success": False, "message": f"Rejected: {reason}"},
            rejected_by,
        )

        return entry

    def get_approval(self, approval_id: str) -> Optional[dict]:
        """Get approval status by ID."""
        return self._pending.get(approval_id)

    def get_all(self, status: Optional[str] = None) -> list[dict]:
        """Get all approvals, optionally filtered by status."""
        approvals = list(self._pending.values())
        if status:
            approvals = [a for a in approvals if a["status"] == status]
        return sorted(approvals, key=lambda x: x["created_at"], reverse=True)

    def get_pending(self) -> list[dict]:
        """Get all pending approvals."""
        return self.get_all(status="pending")

    def _generate_id(self, ctx: AlertContext, plan: RemediationPlan) -> str:
        """Generate a short unique ID for the approval."""
        raw = f"{ctx.alert_name}-{plan.target}-{ctx.namespace}-{datetime.now(timezone.utc).isoformat()}"
        return hashlib.md5(raw.encode()).hexdigest()[:10]


# ── Singleton ─────────────────────────────────────────────────────────────
approval_handler = ApprovalHandler()
