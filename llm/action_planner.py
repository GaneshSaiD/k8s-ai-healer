# llm/action_planner.py
# Converts raw LLM JSON output into a typed RemediationPlan
# Also decides whether to auto-approve or require human approval

import logging
import os
from datetime import datetime, timezone

from webhook.models import AlertContext, RemediationPlan
from llm.groq_client import groq_client

logger = logging.getLogger(__name__)

# Actions safe enough to auto-approve at high confidence (no human needed)
AUTO_APPROVE_ACTIONS = {"restart_pod", "rollout_restart"}
AUTO_APPROVE_CONFIDENCE_THRESHOLD = 0.90

# Actions that always require human approval regardless of confidence
ALWAYS_HUMAN_APPROVAL = {"drain_node", "cordon_node", "patch_resources"}


class ActionPlanner:
    """
    Orchestrates the full reasoning pipeline:
    AlertContext → Groq LLM → RemediationPlan → approval decision
    """

    async def plan(self, ctx: AlertContext) -> RemediationPlan:
        """
        Main entry point — reason over an alert and return a RemediationPlan.
        """
        dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

        logger.info(
            f"Planning remediation for {ctx.alert_name} | "
            f"dry_run={dry_run}"
        )

        # ── Call Groq LLM ─────────────────────────────────────────────────
        llm_output = await groq_client.reason(ctx)

        # ── Build RemediationPlan ─────────────────────────────────────────
        plan = RemediationPlan(
            action=llm_output.get("action", "investigate"),
            target=llm_output.get("target", ctx.pod or ctx.deployment or "unknown"),
            namespace=llm_output.get("namespace", ctx.namespace),
            reason=llm_output.get("reason", ""),
            confidence=float(llm_output.get("confidence", 0.0)),
            dry_run=dry_run,
            approved=False,
        )

        # ── Approval decision ─────────────────────────────────────────────
        plan.approved = self._should_auto_approve(plan)

        if plan.approved:
            logger.info(
                f"Auto-approved: {plan.action} on {plan.target} "
                f"(confidence={plan.confidence:.0%})"
            )
        else:
            logger.info(
                f"Requires human approval: {plan.action} on {plan.target} "
                f"(confidence={plan.confidence:.0%})"
            )

        # Log full plan for audit trail
        self._audit_log(ctx, llm_output, plan)

        return plan

    def _should_auto_approve(self, plan: RemediationPlan) -> bool:
        """
        Decide if a plan can be auto-approved without human review.

        Auto-approve only if ALL of:
        - Not in dry_run mode
        - Action is in safe auto-approve list
        - Confidence is above threshold
        - Action is NOT in always-human-approval list
        """
        if plan.dry_run:
            return False

        if plan.action in ALWAYS_HUMAN_APPROVAL:
            return False

        if plan.action not in AUTO_APPROVE_ACTIONS:
            return False

        if plan.confidence < AUTO_APPROVE_CONFIDENCE_THRESHOLD:
            return False

        return True

    def _audit_log(
        self,
        ctx: AlertContext,
        llm_output: dict,
        plan: RemediationPlan,
    ) -> None:
        """Log full decision for audit trail."""
        logger.info(
            "REMEDIATION PLAN | "
            f"alert={ctx.alert_name} | "
            f"action={plan.action} | "
            f"target={plan.target} | "
            f"namespace={plan.namespace} | "
            f"confidence={plan.confidence:.0%} | "
            f"approved={plan.approved} | "
            f"dry_run={plan.dry_run} | "
            f"reason={plan.reason[:100]}"
        )


# ── Singleton ─────────────────────────────────────────────────────────────
action_planner = ActionPlanner()
