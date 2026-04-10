# webhook/alert_parser.py
# Parses raw AlertManager webhook payload into clean AlertContext objects
# Also enriches context with live cluster state via kubectl

import logging
import subprocess
import json
from datetime import datetime, timezone

from webhook.models import Alert, AlertManagerPayload, AlertContext

logger = logging.getLogger(__name__)


class AlertParser:
    """
    Converts raw AlertManager payload → AlertContext objects
    ready to be passed to the LLM reasoning engine.
    """

    def parse(self, payload: AlertManagerPayload) -> list[AlertContext]:
        """Parse all firing alerts from payload into AlertContext list."""
        contexts = []

        for alert in payload.firing_alerts:
            try:
                ctx = self._build_context(alert)
                contexts.append(ctx)
                logger.info(
                    "Parsed alert",
                    extra={
                        "alert_name": ctx.alert_name,
                        "severity":   ctx.severity,
                        "namespace":  ctx.namespace,
                        "pod":        ctx.pod,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to parse alert {alert.name}: {e}")

        return contexts

    def _build_context(self, alert: Alert) -> AlertContext:
        """Build a single AlertContext, enriched with live cluster info."""
        cluster_info = self._fetch_cluster_info(alert)

        return AlertContext(
            alert_name=alert.name,
            severity=alert.severity,
            namespace=alert.namespace,
            pod=alert.pod,
            deployment=self._infer_deployment(alert),
            node=alert.node,
            action_hint=alert.action,
            summary=alert.summary,
            description=alert.description,
            fired_at=alert.startsAt,
            cluster_info=cluster_info,
        )

    def _infer_deployment(self, alert: Alert) -> str:
        """Try to infer deployment name from pod name (strip hash suffix)."""
        if alert.deployment:
            return alert.deployment
        if alert.pod:
            # Pod name format: <deployment>-<replicaset-hash>-<pod-hash>
            parts = alert.pod.rsplit("-", 2)
            if len(parts) >= 2:
                return parts[0]
        return ""

    def _fetch_cluster_info(self, alert: Alert) -> dict:
        """
        Fetch live cluster state for the affected resource.
        Uses kubectl — works with both local kubeconfig and in-cluster.
        Falls back gracefully if kubectl is unavailable.
        """
        info = {}

        try:
            # Pod details
            if alert.pod and alert.namespace:
                info["pod_describe"] = self._kubectl(
                    ["describe", "pod", alert.pod, "-n", alert.namespace]
                )
                info["pod_logs"] = self._kubectl(
                    ["logs", alert.pod, "-n", alert.namespace,
                     "--tail=20", "--previous"],
                    fallback=self._kubectl(
                        ["logs", alert.pod, "-n", alert.namespace, "--tail=20"]
                    )
                )

            # Deployment details
            deployment = self._infer_deployment(alert)
            if deployment and alert.namespace:
                info["deployment_status"] = self._kubectl(
                    ["get", "deployment", deployment,
                     "-n", alert.namespace, "-o", "json"]
                )

            # Node details
            if alert.node:
                info["node_status"] = self._kubectl(
                    ["describe", "node", alert.node]
                )

            # Namespace-level pod summary
            if alert.namespace:
                info["namespace_pods"] = self._kubectl(
                    ["get", "pods", "-n", alert.namespace,
                     "-o", "wide", "--no-headers"]
                )

        except Exception as e:
            logger.warning(f"Could not fetch cluster info: {e}")
            info["error"] = str(e)

        return info

    def _kubectl(self, args: list[str], fallback: str = "") -> str:
        """Run a kubectl command and return stdout as string."""
        try:
            result = subprocess.run(
                ["kubectl"] + args,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return fallback or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return "kubectl timeout"
        except FileNotFoundError:
            return "kubectl not found"
        except Exception as e:
            return str(e)


# ── Singleton instance ────────────────────────────────────────────────────
alert_parser = AlertParser()
