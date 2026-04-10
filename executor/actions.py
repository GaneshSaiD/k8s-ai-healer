# executor/actions.py
# Kubernetes remediation actions executed by the AI healer
# All actions are safe-by-default: dry_run=True unless explicitly disabled

import logging
import os
from datetime import datetime, timezone

from kubernetes import client
from kubernetes.client.rest import ApiException

from executor.k8s_client import k8s_client
from webhook.models import RemediationPlan

logger = logging.getLogger(__name__)


class ActionResult:
    """Result of a remediation action."""
    def __init__(
        self,
        success:  bool,
        action:   str,
        target:   str,
        message:  str,
        dry_run:  bool = True,
    ):
        self.success   = success
        self.action    = action
        self.target    = target
        self.message   = message
        self.dry_run   = dry_run
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "success":   self.success,
            "action":    self.action,
            "target":    self.target,
            "message":   self.message,
            "dry_run":   self.dry_run,
            "timestamp": self.timestamp,
        }


class K8sActions:
    """
    Executes approved Kubernetes remediation actions.
    Every action checks dry_run flag before touching the cluster.
    """

    def __init__(self):
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    async def execute(self, plan: RemediationPlan) -> ActionResult:
        """
        Route plan to the correct action handler.
        Only executes if plan is approved and not in dry_run mode.
        """
        if not plan.approved:
            return ActionResult(
                success=False,
                action=plan.action,
                target=plan.target,
                message="Action not approved — awaiting human approval via Slack",
                dry_run=self.dry_run,
            )

        logger.info(
            f"Executing action: {plan.action} on {plan.target} "
            f"in {plan.namespace} | dry_run={self.dry_run}"
        )

        handlers = {
            "restart_pod":      self._restart_pod,
            "scale_deployment": self._scale_deployment,
            "rollout_restart":  self._rollout_restart,
            "cordon_node":      self._cordon_node,
            "drain_node":       self._drain_node,
            "patch_resources":  self._patch_resources,
            "investigate":      self._investigate,
        }

        handler = handlers.get(plan.action, self._unknown_action)
        return await handler(plan)

    # ── Action handlers ───────────────────────────────────────────────────

    async def _restart_pod(self, plan: RemediationPlan) -> ActionResult:
        """Delete the pod so it gets rescheduled by the ReplicaSet."""
        if self.dry_run:
            return self._dry_run_result(plan, f"Would delete pod {plan.target} in {plan.namespace}")

        try:
            k8s_client.core.delete_namespaced_pod(
                name=plan.target,
                namespace=plan.namespace,
                body=client.V1DeleteOptions(grace_period_seconds=0),
            )
            msg = f"Pod {plan.target} deleted — will be rescheduled by ReplicaSet"
            logger.info(msg)
            return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

        except ApiException as e:
            msg = f"Failed to delete pod {plan.target}: {e.reason}"
            logger.error(msg)
            return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    async def _scale_deployment(self, plan: RemediationPlan) -> ActionResult:
        """Scale a deployment up by 1 replica."""
        deployment_name = plan.target.rsplit("-", 2)[0] if "-" in plan.target else plan.target

        if self.dry_run:
            return self._dry_run_result(
                plan, f"Would scale deployment {deployment_name} in {plan.namespace} by +1 replica"
            )

        try:
            deploy = k8s_client.get_deployment(deployment_name, plan.namespace)
            if not deploy:
                return ActionResult(
                    False, plan.action, plan.target,
                    f"Deployment {deployment_name} not found", self.dry_run
                )

            current_replicas = deploy.spec.replicas or 1
            new_replicas     = current_replicas + 1

            k8s_client.apps.patch_namespaced_deployment_scale(
                name=deployment_name,
                namespace=plan.namespace,
                body={"spec": {"replicas": new_replicas}},
            )

            msg = f"Scaled {deployment_name} from {current_replicas} to {new_replicas} replicas"
            logger.info(msg)
            return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

        except ApiException as e:
            msg = f"Failed to scale {deployment_name}: {e.reason}"
            logger.error(msg)
            return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    async def _rollout_restart(self, plan: RemediationPlan) -> ActionResult:
        """Trigger a rolling restart of a deployment via patch annotation."""
        deployment_name = plan.target.rsplit("-", 2)[0] if "-" in plan.target else plan.target

        if self.dry_run:
            return self._dry_run_result(
                plan, f"Would rollout restart deployment {deployment_name}"
            )

        try:
            # Patch the deployment template annotation to trigger a rollout
            patch_body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt":
                                    datetime.now(timezone.utc).isoformat()
                            }
                        }
                    }
                }
            }

            k8s_client.apps.patch_namespaced_deployment(
                name=deployment_name,
                namespace=plan.namespace,
                body=patch_body,
            )

            msg = f"Rollout restart triggered for deployment {deployment_name}"
            logger.info(msg)
            return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

        except ApiException as e:
            msg = f"Failed to rollout restart {deployment_name}: {e.reason}"
            logger.error(msg)
            return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    async def _cordon_node(self, plan: RemediationPlan) -> ActionResult:
        """Mark a node as unschedulable (cordon)."""
        if self.dry_run:
            return self._dry_run_result(plan, f"Would cordon node {plan.target}")

        try:
            k8s_client.core.patch_node(
                name=plan.target,
                body={"spec": {"unschedulable": True}},
            )
            msg = f"Node {plan.target} cordoned — no new pods will be scheduled"
            logger.info(msg)
            return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

        except ApiException as e:
            msg = f"Failed to cordon node {plan.target}: {e.reason}"
            logger.error(msg)
            return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    async def _drain_node(self, plan: RemediationPlan) -> ActionResult:
        """Cordon node and evict all pods (drain)."""
        if self.dry_run:
            return self._dry_run_result(
                plan, f"Would drain node {plan.target} (cordon + evict all pods)"
            )

        try:
            # Step 1 — cordon
            k8s_client.core.patch_node(
                name=plan.target,
                body={"spec": {"unschedulable": True}},
            )

            # Step 2 — evict all pods on the node (except DaemonSets)
            pods = k8s_client.core.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={plan.target}"
            ).items

            evicted = 0
            for pod in pods:
                # Skip DaemonSet pods — they can't be evicted
                owner_kinds = [
                    ref.kind for ref in (pod.metadata.owner_references or [])
                ]
                if "DaemonSet" in owner_kinds:
                    continue

                try:
                    k8s_client.core.create_namespaced_pod_eviction(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                        body=client.V1Eviction(
                            metadata=client.V1ObjectMeta(
                                name=pod.metadata.name,
                                namespace=pod.metadata.namespace,
                            )
                        ),
                    )
                    evicted += 1
                except ApiException:
                    pass

            msg = f"Node {plan.target} drained — cordoned + evicted {evicted} pods"
            logger.info(msg)
            return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

        except ApiException as e:
            msg = f"Failed to drain node {plan.target}: {e.reason}"
            logger.error(msg)
            return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    async def _patch_resources(self, plan: RemediationPlan) -> ActionResult:
        """Patch resource limits on a deployment to prevent OOM kills."""
        deployment_name = plan.target.rsplit("-", 2)[0] if "-" in plan.target else plan.target

        if self.dry_run:
            return self._dry_run_result(
                plan, f"Would patch resource limits on {deployment_name}"
            )

        try:
            deploy = k8s_client.get_deployment(deployment_name, plan.namespace)
            if not deploy:
                return ActionResult(
                    False, plan.action, plan.target,
                    f"Deployment {deployment_name} not found", self.dry_run
                )

            # Double the memory limit for the first container
            containers = deploy.spec.template.spec.containers
            if containers and containers[0].resources and containers[0].resources.limits:
                current_mem = containers[0].resources.limits.get("memory", "128Mi")
                # Simple doubling — parse Mi value
                if current_mem.endswith("Mi"):
                    new_mem = f"{int(current_mem[:-2]) * 2}Mi"
                else:
                    new_mem = "256Mi"

                patch_body = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [{
                                    "name": containers[0].name,
                                    "resources": {
                                        "limits":   {"memory": new_mem},
                                        "requests": {"memory": new_mem},
                                    }
                                }]
                            }
                        }
                    }
                }

                k8s_client.apps.patch_namespaced_deployment(
                    name=deployment_name,
                    namespace=plan.namespace,
                    body=patch_body,
                )

                msg = f"Patched {deployment_name} memory limit: {current_mem} → {new_mem}"
                logger.info(msg)
                return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

            return ActionResult(
                False, plan.action, plan.target,
                "No resource limits found to patch", self.dry_run
            )

        except ApiException as e:
            msg = f"Failed to patch resources on {deployment_name}: {e.reason}"
            logger.error(msg)
            return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    async def _investigate(self, plan: RemediationPlan) -> ActionResult:
        """No automated action — return cluster state summary for human review."""
        pods = k8s_client.get_pods(plan.namespace)
        pod_summary = "\n".join([
            f"  {p.metadata.name}: {p.status.phase}"
            for p in pods[:10]
        ])
        msg = (
            f"Investigation requested for {plan.target} in {plan.namespace}.\n"
            f"Current pods:\n{pod_summary}\n"
            f"Reason: {plan.reason}"
        )
        logger.info(msg)
        return ActionResult(True, plan.action, plan.target, msg, self.dry_run)

    async def _unknown_action(self, plan: RemediationPlan) -> ActionResult:
        msg = f"Unknown action: {plan.action} — no handler found"
        logger.warning(msg)
        return ActionResult(False, plan.action, plan.target, msg, self.dry_run)

    def _dry_run_result(self, plan: RemediationPlan, message: str) -> ActionResult:
        logger.info(f"[DRY RUN] {message}")
        return ActionResult(
            success=True,
            action=plan.action,
            target=plan.target,
            message=f"[DRY RUN] {message}",
            dry_run=True,
        )


# ── Singleton ─────────────────────────────────────────────────────────────
k8s_actions = K8sActions()
