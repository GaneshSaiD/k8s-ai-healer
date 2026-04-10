# approvals/slack_notifier.py
# Sends AI remediation plans to Slack with Approve/Reject buttons
# Uses Slack Incoming Webhooks (free, no OAuth needed)

import hashlib
import json
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from webhook.models import AlertContext, RemediationPlan

load_dotenv()
logger = logging.getLogger(__name__)


class SlackNotifier:
    """
    Sends remediation plan approval requests to Slack.
    Uses Block Kit for rich interactive messages.
    """

    def __init__(self):
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.channel     = os.getenv("SLACK_CHANNEL", "#k8s-alerts")

        if not self.webhook_url:
            logger.warning("SLACK_WEBHOOK_URL not set — Slack notifications disabled")

    async def send_approval_request(
        self,
        ctx: AlertContext,
        plan: RemediationPlan,
        approval_id: str,
    ) -> bool:
        """
        Send an approval request to Slack with Approve/Reject buttons.
        Returns True if message was sent successfully.
        """
        if not self.webhook_url:
            logger.warning("Skipping Slack notification — no webhook URL configured")
            return False

        message = self._build_approval_message(ctx, plan, approval_id)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.webhook_url,
                    json=message,
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code == 200:
                    logger.info(f"Slack approval request sent for {ctx.alert_name}")
                    return True
                else:
                    logger.error(
                        f"Slack webhook failed: {response.status_code} {response.text}"
                    )
                    return False

        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False

    async def send_resolution(
        self,
        ctx: AlertContext,
        plan: RemediationPlan,
        result: dict,
        approved_by: str = "system",
    ) -> bool:
        """Send a resolution message after action is executed."""
        if not self.webhook_url:
            return False

        success = result.get("success", False)
        emoji   = "✅" if success else "❌"
        color   = "#36a64f" if success else "#d00000"

        message = {
            "text": f"{emoji} Remediation {'succeeded' if success else 'failed'}: {ctx.alert_name}",
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Alert:*\n{ctx.alert_name}"},
                                {"type": "mrkdwn", "text": f"*Action:*\n`{plan.action}`"},
                                {"type": "mrkdwn", "text": f"*Target:*\n`{plan.target}`"},
                                {"type": "mrkdwn", "text": f"*Approved by:*\n{approved_by}"},
                            ]
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Result:*\n{result.get('message', 'No details')}"
                            }
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"Executed at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.webhook_url, json=message)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send resolution notification: {e}")
            return False

    async def send_alert_notification(self, ctx: AlertContext) -> bool:
        """Send a simple alert notification (no approval needed — investigate actions)."""
        if not self.webhook_url:
            return False

        severity_emoji = {
            "critical": "🔴",
            "warning":  "🟡",
            "info":     "🔵",
        }.get(ctx.severity, "⚪")

        message = {
            "text": f"{severity_emoji} K8s Alert: {ctx.alert_name}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{severity_emoji} *{ctx.alert_name}* in `{ctx.namespace}`\n"
                            f"{ctx.summary}"
                        )
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"Pod: `{ctx.pod or 'N/A'}` | "
                                f"Severity: *{ctx.severity.upper()}* | "
                                f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                            )
                        }
                    ]
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.webhook_url, json=message)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send alert notification: {e}")
            return False

    def _build_approval_message(
        self,
        ctx: AlertContext,
        plan: RemediationPlan,
        approval_id: str,
    ) -> dict:
        """Build a rich Slack Block Kit approval message."""
        severity_emoji = {
            "critical": "🔴",
            "warning":  "🟡",
            "info":     "🔵",
        }.get(ctx.severity, "⚪")

        confidence_pct  = int(plan.confidence * 100)
        confidence_bar  = self._confidence_bar(plan.confidence)
        impact          = "unknown"

        # Approval/reject URLs pointing to our FastAPI endpoint
        base_url    = os.getenv("WEBHOOK_URL", "http://localhost:8000")
        approve_url = f"{base_url}/approvals/{approval_id}/approve"
        reject_url  = f"{base_url}/approvals/{approval_id}/reject"

        return {
            "text": f"{severity_emoji} AI Remediation Plan — {ctx.alert_name}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{severity_emoji} AI Remediation Plan Ready",
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Alert:*\n{ctx.alert_name}"},
                        {"type": "mrkdwn", "text": f"*Severity:*\n{ctx.severity.upper()}"},
                        {"type": "mrkdwn", "text": f"*Namespace:*\n`{ctx.namespace}`"},
                        {"type": "mrkdwn", "text": f"*Pod:*\n`{ctx.pod or 'N/A'}`"},
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Summary:*\n{ctx.summary}"
                    }
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Proposed Action:*\n`{plan.action}`"},
                        {"type": "mrkdwn", "text": f"*Target:*\n`{plan.target}`"},
                        {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence_bar} {confidence_pct}%"},
                        {"type": "mrkdwn", "text": f"*Dry Run:*\n{'Yes ✅' if plan.dry_run else 'No ⚠️'}"},
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*🧠 AI Reasoning:*\n_{plan.reason}_"
                    }
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Approval ID:* `{approval_id}`\n"
                            f"Approve → `POST {approve_url}`\n"
                            f"Reject  → `POST {reject_url}`"
                        )
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"K8s AI Healer • "
                                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} • "
                                f"Sister project: k8s-rag-chatbot"
                            )
                        }
                    ]
                }
            ]
        }

    def _confidence_bar(self, confidence: float) -> str:
        """Visual confidence bar using emoji blocks."""
        filled = int(confidence * 10)
        return "█" * filled + "░" * (10 - filled)


# ── Singleton ─────────────────────────────────────────────────────────────
slack_notifier = SlackNotifier()
