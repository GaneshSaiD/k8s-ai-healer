# webhook/models.py
# Pydantic models matching AlertManager webhook payload schema exactly
# Docs: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# ── Alert status ──────────────────────────────────────────────────────────
class AlertStatus:
    FIRING   = "firing"
    RESOLVED = "resolved"


# ── Single alert from AlertManager ───────────────────────────────────────
class Alert(BaseModel):
    status:       str                    # "firing" | "resolved"
    labels:       dict[str, str]         # alertname, severity, namespace, pod, etc.
    annotations:  dict[str, str]         # summary, description, runbook
    startsAt:     datetime
    endsAt:       datetime
    generatorURL: str = ""
    fingerprint:  str = ""

    # Convenience properties
    @property
    def name(self) -> str:
        return self.labels.get("alertname", "UnknownAlert")

    @property
    def severity(self) -> str:
        return self.labels.get("severity", "unknown")

    @property
    def namespace(self) -> str:
        return self.labels.get("namespace", "default")

    @property
    def pod(self) -> str:
        return self.labels.get("pod", "")

    @property
    def deployment(self) -> str:
        return self.labels.get("deployment", "")

    @property
    def node(self) -> str:
        return self.labels.get("instance", "")

    @property
    def action(self) -> str:
        """The remediation action tag we set in alert-rules.yaml"""
        return self.labels.get("action", "investigate")

    @property
    def summary(self) -> str:
        return self.annotations.get("summary", self.name)

    @property
    def description(self) -> str:
        return self.annotations.get("description", "")

    @property
    def is_firing(self) -> bool:
        return self.status == "firing"


# ── Full AlertManager webhook payload ─────────────────────────────────────
class AlertManagerPayload(BaseModel):
    version:           str = "4"
    groupKey:          str = ""
    truncatedAlerts:   int = 0
    status:            str                    # "firing" | "resolved"
    receiver:          str
    groupLabels:       dict[str, str] = {}
    commonLabels:      dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL:       str = ""
    alerts:            list[Alert]

    @property
    def firing_alerts(self) -> list[Alert]:
        return [a for a in self.alerts if a.is_firing]

    @property
    def resolved_alerts(self) -> list[Alert]:
        return [a for a in self.alerts if not a.is_firing]

    @property
    def has_critical(self) -> bool:
        return any(a.severity == "critical" for a in self.firing_alerts)


# ── Parsed alert context (what we pass to the LLM) ────────────────────────
class AlertContext(BaseModel):
    alert_name:   str
    severity:     str
    namespace:    str
    pod:          str        = ""
    deployment:   str        = ""
    node:         str        = ""
    action_hint:  str        = "investigate"   # from alert label
    summary:      str
    description:  str
    fired_at:     datetime
    cluster_info: dict[str, Any] = {}          # filled by alert_parser


# ── Remediation decision (returned by LLM) ────────────────────────────────
class RemediationPlan(BaseModel):
    action:       str                  # restart_pod | scale_deployment | cordon_node | investigate
    target:       str                  # pod name, deployment name, or node name
    namespace:    str
    reason:       str                  # LLM explanation
    confidence:   float = 0.0          # 0.0 - 1.0
    dry_run:      bool  = True         # always True until human approves
    approved:     bool  = False


# ── Webhook response ──────────────────────────────────────────────────────
class WebhookResponse(BaseModel):
    status:     str
    message:    str
    alert_count: int = 0
    plans:      list[RemediationPlan] = []
