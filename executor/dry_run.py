# executor/dry_run.py
# Dry run validator — simulates what an action WOULD do without touching the cluster
# Used for testing, demos, and pre-approval validation

import logging
from datetime import datetime, timezone

from executor.k8s_client import k8s_client
from webhook.models import RemediationPlan

logger = logging.getLogger(__name__)


class DryRunValidator:
    """
    Validates a RemediationPlan against live cluster state
    and returns a detailed simulation of what would happen.
    """

    async def simulate(self, plan: RemediationPlan) -> dict:
        """
        Simulate the action and return what would happen.
        Fetches live cluster state to make simulation realistic.
        """
        logger.info(f"Simulating: {plan.action} on {plan.target}")

        simulators = {
            "restart_pod":      self._simulate_restart_pod,
            "scale_deployment": self._simulate_scale_deployment,
            "rollout_restart":  self._simulate_rollout_restart,
            "cordon_node":      self._simulate_cordon_node,
            "drain_node":       self._simulate_drain_node,
            "patch_resources":  self._simulate_patch_resources,
            "investigate":      self._simulate_investigate,
        }

        simulator = simulators.get(plan.action, self._simulate_unknown)
        result = await simulator(plan)

        return {
            "plan":        plan.dict(),
            "simulation":  result,
            "simulated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _simulate_restart_pod(self, plan: RemediationPlan) -> dict:
        pod = k8s_client.get_pod(plan.target, plan.namespace)
        if not pod:
            return {
                "feasible": False,
                "reason":   f"Pod {plan.target} not found in {plan.namespace}",
            }

        restarts = 0
        for cs in (pod.status.container_statuses or []):
            restarts += cs.restart_count or 0

        return {
            "feasible":         True,
            "current_state":    pod.status.phase,
            "restart_count":    restarts,
            "would_do":         f"DELETE pod/{plan.target} -n {plan.namespace} --grace-period=0",
            "expected_outcome": "Pod deleted, ReplicaSet schedules a fresh replacement within ~10s",
            "risk":             "Low — ReplicaSet ensures availability",
            "rollback":         "N/A — pod auto-recreated by ReplicaSet",
        }

    async def _simulate_scale_deployment(self, plan: RemediationPlan) -> dict:
        deployment_name = plan.target.rsplit("-", 2)[0] if "-" in plan.target else plan.target
        deploy = k8s_client.get_deployment(deployment_name, plan.namespace)

        if not deploy:
            return {
                "feasible": False,
                "reason":   f"Deployment {deployment_name} not found",
            }

        current = deploy.spec.replicas or 1
        return {
            "feasible":         True,
            "deployment":       deployment_name,
            "current_replicas": current,
            "new_replicas":     current + 1,
            "would_do":         f"kubectl scale deployment/{deployment_name} --replicas={current + 1} -n {plan.namespace}",
            "expected_outcome": f"Deployment scaled from {current} to {current + 1} replicas",
            "risk":             "Low — adds capacity without removing existing pods",
            "rollback":         f"kubectl scale deployment/{deployment_name} --replicas={current} -n {plan.namespace}",
        }

    async def _simulate_rollout_restart(self, plan: RemediationPlan) -> dict:
        deployment_name = plan.target.rsplit("-", 2)[0] if "-" in plan.target else plan.target
        deploy = k8s_client.get_deployment(deployment_name, plan.namespace)

        if not deploy:
            return {
                "feasible": False,
                "reason":   f"Deployment {deployment_name} not found",
            }

        return {
            "feasible":         True,
            "deployment":       deployment_name,
            "current_replicas": deploy.spec.replicas,
            "would_do":         f"kubectl rollout restart deployment/{deployment_name} -n {plan.namespace}",
            "expected_outcome": "Rolling restart — pods replaced one by one, zero downtime",
            "risk":             "Low — rolling strategy maintains availability",
            "rollback":         f"kubectl rollout undo deployment/{deployment_name} -n {plan.namespace}",
        }

    async def _simulate_cordon_node(self, plan: RemediationPlan) -> dict:
        node = k8s_client.get_node(plan.target)
        if not node:
            return {"feasible": False, "reason": f"Node {plan.target} not found"}

        pods_on_node = k8s_client.core.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={plan.target}"
        ).items

        return {
            "feasible":         True,
            "node":             plan.target,
            "currently_schedulable": not node.spec.unschedulable,
            "pods_on_node":     len(pods_on_node),
            "would_do":         f"kubectl cordon {plan.target}",
            "expected_outcome": "Node marked unschedulable — existing pods unaffected, no new pods scheduled here",
            "risk":             "Low — existing workloads continue running",
            "rollback":         f"kubectl uncordon {plan.target}",
        }

    async def _simulate_drain_node(self, plan: RemediationPlan) -> dict:
        node = k8s_client.get_node(plan.target)
        if not node:
            return {"feasible": False, "reason": f"Node {plan.target} not found"}

        pods_on_node = k8s_client.core.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={plan.target}"
        ).items

        daemonset_pods = sum(
            1 for p in pods_on_node
            if any(r.kind == "DaemonSet" for r in (p.metadata.owner_references or []))
        )
        evictable_pods = len(pods_on_node) - daemonset_pods

        return {
            "feasible":         True,
            "node":             plan.target,
            "total_pods":       len(pods_on_node),
            "daemonset_pods":   daemonset_pods,
            "evictable_pods":   evictable_pods,
            "would_do":         f"kubectl drain {plan.target} --ignore-daemonsets --delete-emptydir-data",
            "expected_outcome": f"Node cordoned + {evictable_pods} pods evicted and rescheduled on other nodes",
            "risk":             "High — all workloads moved, may cause brief disruption",
            "rollback":         f"kubectl uncordon {plan.target}",
        }

    async def _simulate_patch_resources(self, plan: RemediationPlan) -> dict:
        deployment_name = plan.target.rsplit("-", 2)[0] if "-" in plan.target else plan.target
        deploy = k8s_client.get_deployment(deployment_name, plan.namespace)

        if not deploy:
            return {"feasible": False, "reason": f"Deployment {deployment_name} not found"}

        containers = deploy.spec.template.spec.containers
        if not containers:
            return {"feasible": False, "reason": "No containers found in deployment"}

        c = containers[0]
        current_mem = "unknown"
        if c.resources and c.resources.limits:
            current_mem = c.resources.limits.get("memory", "unknown")

        new_mem = "256Mi"
        if current_mem.endswith("Mi"):
            new_mem = f"{int(current_mem[:-2]) * 2}Mi"

        return {
            "feasible":         True,
            "deployment":       deployment_name,
            "container":        c.name,
            "current_memory_limit": current_mem,
            "new_memory_limit": new_mem,
            "would_do":         f"kubectl patch deployment/{deployment_name} with memory limit {current_mem}→{new_mem}",
            "expected_outcome": "Deployment rolling restarted with doubled memory limit",
            "risk":             "Low — increases resource allocation, rolling restart",
            "rollback":         f"kubectl patch deployment/{deployment_name} with memory limit {new_mem}→{current_mem}",
        }

    async def _simulate_investigate(self, plan: RemediationPlan) -> dict:
        return {
            "feasible":         True,
            "would_do":         "No automated action — human review required",
            "expected_outcome": "Incident logged, team notified via Slack",
            "risk":             "None",
            "rollback":         "N/A",
        }

    async def _simulate_unknown(self, plan: RemediationPlan) -> dict:
        return {
            "feasible": False,
            "reason":   f"Unknown action: {plan.action}",
        }


# ── Singleton ─────────────────────────────────────────────────────────────
dry_run_validator = DryRunValidator()
