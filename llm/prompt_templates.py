# llm/prompt_templates.py
# Prompt templates for Groq LLM reasoning over Kubernetes alerts
# Designed for LLaMA 3.1 70B — structured output via JSON mode

from webhook.models import AlertContext


# ── System prompt ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert Kubernetes Site Reliability Engineer (SRE) \
with deep knowledge of Kubernetes internals, pod lifecycle, deployment strategies, \
node management, and production incident response.

Your job is to analyze Kubernetes alerts and produce a precise, safe remediation plan.

RULES:
1. Always respond with valid JSON only — no markdown, no explanation outside JSON.
2. Be conservative — prefer the least disruptive action that fixes the problem.
3. Set confidence between 0.0 and 1.0 based on how certain you are.
4. If you are unsure, set action to "investigate" and confidence below 0.5.
5. Never suggest destructive actions (delete namespace, delete PV, etc).
6. Always include a clear reason explaining your decision.

AVAILABLE ACTIONS:
- restart_pod        → Delete pod so it gets rescheduled (use for crash loops)
- scale_deployment   → Increase replica count (use for OOM or replica mismatch)
- rollout_restart    → kubectl rollout restart deployment (use for stuck rollouts)
- cordon_node        → Mark node unschedulable (use for high CPU/memory pressure)
- drain_node         → Cordon + evict all pods (use for critical node issues)
- patch_resources    → Update resource limits/requests (use for OOM issues)
- investigate        → No automated action — needs human review

RESPONSE FORMAT (strict JSON):
{
  "action": "<action from list above>",
  "target": "<pod name, deployment name, or node name>",
  "namespace": "<kubernetes namespace>",
  "reason": "<clear explanation of why this action was chosen>",
  "confidence": <float 0.0-1.0>,
  "additional_context": "<any extra info the operator should know>",
  "estimated_impact": "<low|medium|high>",
  "rollback_plan": "<how to undo this action if it makes things worse>"
}"""


def build_reasoning_prompt(ctx: AlertContext) -> str:
    """
    Build the user prompt for LLM reasoning.
    Includes alert details + live cluster context.
    """

    # Core alert info
    prompt = f"""KUBERNETES ALERT REQUIRING REMEDIATION:

Alert Name:  {ctx.alert_name}
Severity:    {ctx.severity}
Namespace:   {ctx.namespace}
Pod:         {ctx.pod or 'N/A'}
Deployment:  {ctx.deployment or 'N/A'}
Node:        {ctx.node or 'N/A'}
Fired At:    {ctx.fired_at}
Action Hint: {ctx.action_hint}

Summary:
{ctx.summary}

Description:
{ctx.description}
"""

    # Append live cluster context if available
    if ctx.cluster_info:

        if ctx.cluster_info.get("namespace_pods"):
            prompt += f"""
CURRENT PODS IN NAMESPACE ({ctx.namespace}):
{ctx.cluster_info['namespace_pods'][:1000]}
"""

        if ctx.cluster_info.get("pod_describe"):
            prompt += f"""
POD DESCRIBE OUTPUT (last 80 lines):
{_truncate(ctx.cluster_info['pod_describe'], 3000)}
"""

        if ctx.cluster_info.get("pod_logs"):
            prompt += f"""
POD LOGS (last 20 lines):
{_truncate(ctx.cluster_info['pod_logs'], 1000)}
"""

        if ctx.cluster_info.get("deployment_status"):
            prompt += f"""
DEPLOYMENT STATUS (JSON):
{_truncate(ctx.cluster_info['deployment_status'], 1500)}
"""

        if ctx.cluster_info.get("node_status"):
            prompt += f"""
NODE STATUS:
{_truncate(ctx.cluster_info['node_status'], 1500)}
"""

    prompt += """
Based on all the above information, provide the optimal remediation plan as JSON.
Remember: respond with JSON only."""

    return prompt


def build_slack_message(ctx: AlertContext, plan: dict) -> dict:
    """
    Build a Slack Block Kit message for the human approval gate.
    Returns a dict ready to POST to Slack webhook.
    """
    severity_emoji = {
        "critical": "🔴",
        "warning":  "🟡",
        "info":     "🔵",
    }.get(ctx.severity, "⚪")

    confidence_pct = int(plan.get("confidence", 0) * 100)
    impact = plan.get("estimated_impact", "unknown").upper()

    return {
        "text": f"{severity_emoji} K8s Alert: {ctx.alert_name} in {ctx.namespace}",
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
                    {"type": "mrkdwn", "text": f"*Namespace:*\n{ctx.namespace}"},
                    {"type": "mrkdwn", "text": f"*Pod:*\n{ctx.pod or 'N/A'}"},
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
                    {"type": "mrkdwn", "text": f"*Proposed Action:*\n`{plan.get('action')}`"},
                    {"type": "mrkdwn", "text": f"*Target:*\n`{plan.get('target')}`"},
                    {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence_pct}%"},
                    {"type": "mrkdwn", "text": f"*Impact:*\n{impact}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Reasoning:*\n{plan.get('reason', 'N/A')}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Rollback Plan:*\n{plan.get('rollback_plan', 'N/A')}"
                }
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        "value": "approve",
                        "action_id": "approve_remediation",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "value": "reject",
                        "action_id": "reject_remediation",
                    },
                ]
            }
        ]
    }


def _truncate(text: str, max_chars: int) -> str:
    """Truncate long kubectl output to avoid token limit."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated — {len(text) - max_chars} chars omitted]"
