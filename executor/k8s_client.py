# executor/k8s_client.py
# Kubernetes client setup — works with local kubeconfig (Minikube) and in-cluster
# Uses the official kubernetes Python client library

import logging
import os

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class K8sClient:
    """
    Wraps the kubernetes Python client.
    Auto-detects local vs in-cluster config based on K8S_CONFIG env var.
    """

    def __init__(self):
        self._load_config()
        self.core   = client.CoreV1Api()
        self.apps   = client.AppsV1Api()
        self.policy = client.PolicyV1Api()
        logger.info("Kubernetes client initialized")

    def _load_config(self):
        k8s_config = os.getenv("K8S_CONFIG", "local")
        if k8s_config == "incluster":
            config.load_incluster_config()
            logger.info("Loaded in-cluster kubeconfig")
        else:
            config.load_kube_config()
            logger.info("Loaded local kubeconfig (Minikube)")

    def get_pod(self, name: str, namespace: str):
        """Get a single pod by name."""
        try:
            return self.core.read_namespaced_pod(name=name, namespace=namespace)
        except ApiException as e:
            logger.error(f"Failed to get pod {name}/{namespace}: {e}")
            return None

    def get_pods(self, namespace: str, label_selector: str = "") -> list:
        """List pods in a namespace, optionally filtered by label."""
        try:
            result = self.core.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )
            return result.items
        except ApiException as e:
            logger.error(f"Failed to list pods in {namespace}: {e}")
            return []

    def get_deployment(self, name: str, namespace: str):
        """Get a deployment by name."""
        try:
            return self.apps.read_namespaced_deployment(
                name=name, namespace=namespace
            )
        except ApiException as e:
            logger.error(f"Failed to get deployment {name}/{namespace}: {e}")
            return None

    def get_node(self, name: str):
        """Get a node by name."""
        try:
            return self.core.read_node(name=name)
        except ApiException as e:
            logger.error(f"Failed to get node {name}: {e}")
            return None

    def get_nodes(self) -> list:
        """List all nodes."""
        try:
            return self.core.list_node().items
        except ApiException as e:
            logger.error(f"Failed to list nodes: {e}")
            return []


# ── Singleton ─────────────────────────────────────────────────────────────
k8s_client = K8sClient()
