#!/bin/bash
# infra/setup.sh
# One-shot script to bootstrap the entire Phase 1 observability stack on Minikube
# Usage: chmod +x infra/setup.sh && ./infra/setup.sh

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Prerequisites check ────────────────────────────────────────────────────
info "Checking prerequisites..."

command -v minikube >/dev/null 2>&1 || error "minikube not found. Install from https://minikube.sigs.k8s.io"
command -v kubectl  >/dev/null 2>&1 || error "kubectl not found."
command -v helm     >/dev/null 2>&1 || error "helm not found. Install from https://helm.sh"

# ── Start Minikube ─────────────────────────────────────────────────────────
info "Starting Minikube..."
minikube status | grep -q "Running" && info "Minikube already running." || \
  minikube start \
    --cpus=4 \
    --memory=6144 \
    --driver=docker \
    --kubernetes-version=v1.28.0 \
    --addons=metrics-server

info "Enabling Minikube addons..."
minikube addons enable metrics-server
minikube addons enable ingress

# ── Namespaces ────────────────────────────────────────────────────────────
info "Creating namespaces..."
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace demo       --dry-run=client -o yaml | kubectl apply -f -

# ── Helm repos ────────────────────────────────────────────────────────────
info "Adding Helm repos..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add stable https://charts.helm.sh/stable
helm repo update

# ── Prometheus + AlertManager via kube-prometheus-stack ───────────────────
info "Installing kube-prometheus-stack..."
helm upgrade --install kube-prometheus-stack \
  prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --values infra/prometheus/values.yaml \
  --values infra/alertmanager/values.yaml \
  --set prometheus.prometheusSpec.retention=24h \
  --wait \
  --timeout=300s

# ── Apply custom alert rules ───────────────────────────────────────────────
info "Applying custom alert rules..."
kubectl apply -f infra/prometheus/alert-rules.yaml

# ── Deploy sample apps ────────────────────────────────────────────────────
info "Deploying sample apps (crashloop + healthy + oom)..."
kubectl apply -f infra/sample-app/crashloop-app.yaml

# ── Port-forward helpers (background) ─────────────────────────────────────
info "Setting up port-forwards..."
# Prometheus UI → http://localhost:9090
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090 &
PF_PROM=$!

# AlertManager UI → http://localhost:9093
kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 9093:9093 &
PF_AM=$!

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Phase 1 setup complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Prometheus UI  →  http://localhost:9090"
echo "  AlertManager   →  http://localhost:9093"
echo "  Minikube dash  →  run: minikube dashboard"
echo ""
echo "  Crashloop app deployed in 'demo' namespace."
echo "  Alerts will fire within ~2 minutes."
echo ""
echo "  To watch alerts firing:"
echo "    kubectl get pods -n demo -w"
echo "    kubectl describe pod -n demo -l app=crashloop-app"
echo ""
echo -e "${YELLOW}  Port-forwards running (PIDs: $PF_PROM, $PF_AM)${NC}"
echo -e "${YELLOW}  Kill with: kill $PF_PROM $PF_AM${NC}"
echo ""

# Keep port-forwards alive
wait
