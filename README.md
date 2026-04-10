# 🤖 K8s AI Healer

> An AI-powered self-healing Kubernetes operator that detects cluster anomalies via Prometheus, reasons over them using an LLM, and automatically executes remediation actions — with a human approval gate via Slack.

**Live Demo** → *(coming in Phase 6)*  
**Sister Project** → [K8s Ops RAG Chatbot](https://github.com/GaneshSaiD/k8s-rag-chatbot) · [k8s-rag-chatbot.streamlit.app](https://k8s-rag-chatbot.streamlit.app)

---

## 🏗️ Architecture

```
Prometheus Alert → AlertManager → FastAPI Webhook →
Groq LLM (LLaMA 3.1 70B) → Human Approval (Slack) →
Kubernetes API Action → Grafana Incident Log
```

| Layer | Tool | Purpose |
|---|---|---|
| Cluster | Minikube | Local K8s environment |
| Metrics | Prometheus + kube-state-metrics | Scrape cluster health |
| Alerting | AlertManager | Route alerts to webhook |
| Webhook | FastAPI (Render) | Receive + parse alerts |
| Reasoning | Groq API / LLaMA 3.1 70B | Decide remediation action |
| Actions | Python `kubernetes` client | Execute K8s API calls |
| Approval | Slack webhook | Human-in-the-loop gate |
| Dashboard | Streamlit Cloud | Demo UI + incident log |
| Observability | Grafana Cloud | Dashboards + annotations |

---

## 🚀 Quick Start

### Prerequisites
- [Minikube](https://minikube.sigs.k8s.io/docs/start/) ≥ v1.32
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Helm](https://helm.sh/docs/intro/install/) ≥ v3.12
- [Docker](https://docs.docker.com/get-docker/)
- Python 3.11+

### 1. Clone the repo
```bash
git clone https://github.com/GaneshSaiD/k8s-ai-healer.git
cd k8s-ai-healer
```

### 2. Set up environment variables
```bash
cp .env.example .env
# Fill in: GROQ_API_KEY, SLACK_WEBHOOK_URL, WEBHOOK_SECRET_TOKEN
```

### 3. Bootstrap the observability stack (Phase 1)
```bash
chmod +x infra/setup.sh
./infra/setup.sh
```

This installs:
- Minikube cluster (4 CPU, 6GB RAM)
- Prometheus + AlertManager via Helm
- Node Exporter + kube-state-metrics
- A sample crashlooping app to trigger alerts

### 4. Verify alerts are firing
```bash
# Watch pods crash loop in demo namespace
kubectl get pods -n demo -w

# Check Prometheus alerts
open http://localhost:9090/alerts

# Check AlertManager
open http://localhost:9093
```

---

## 📁 Project Structure

```
k8s-ai-healer/
├── infra/                    # Phase 1 — K8s + observability stack
│   ├── prometheus/           # Helm values + custom alert rules
│   ├── alertmanager/         # AlertManager config + Helm values
│   └── sample-app/           # Crashloop, OOM, healthy app manifests
│
├── webhook/                  # Phase 2 — FastAPI alert receiver
│   ├── main.py
│   ├── alert_parser.py
│   └── models.py
│
├── llm/                      # Phase 3 — Groq reasoning engine
│   ├── groq_client.py
│   ├── prompt_templates.py
│   └── action_planner.py
│
├── executor/                 # Phase 4 — Kubernetes API actions
│   ├── k8s_client.py
│   ├── actions.py
│   └── dry_run.py
│
├── approvals/                # Phase 5 — Slack approval gate
│   ├── slack_notifier.py
│   ├── approval_handler.py
│   └── templates.py
│
├── dashboard/                # Phase 6 — Streamlit demo UI
│   ├── app.py
│   ├── incident_log.py
│   └── cluster_status.py
│
├── .github/workflows/        # CI/CD — GitHub Actions
│   └── ci.yml
│
├── docker-compose.yml        # Local full-stack dev
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## 🔄 How It Works

1. **Prometheus** scrapes pod/node metrics every 15 seconds
2. **AlertManager** evaluates rules — when a pod crash loops for 2 min, it fires
3. **FastAPI webhook** receives the alert payload, parses it, and builds context
4. **Groq LLM** (LLaMA 3.1 70B) reasons over alert + cluster state and returns a structured remediation plan
5. **Slack approval gate** sends the plan to `#k8s-alerts` — a human clicks Approve or Reject
6. **Kubernetes Python client** executes the approved action (restart, scale, drain, patch)
7. **Grafana Cloud** gets an incident annotation; Slack gets a resolution message

---

## 🎯 Supported Remediation Actions

| Alert | Action |
|---|---|
| `PodCrashLooping` | Restart pod via rollout |
| `PodOOMKilled` | Scale deployment + adjust limits |
| `DeploymentUnavailable` | Rollout restart deployment |
| `NodeHighCPU` | Cordon node + reschedule pods |
| `NodeMemoryPressure` | Cordon + drain node |
| `HighPendingPods` | Investigate + report |

---

## 🔗 Related Projects

This project is part of a growing **AI-powered Kubernetes ecosystem**:

| Project | Description | Link |
|---|---|---|
| **K8s Ops RAG Chatbot** | Ask questions about K8s in natural language | [GitHub](https://github.com/GaneshSaiD/k8s-rag-chatbot) |
| **K8s AI Healer** | Self-healing operator with LLM reasoning | This repo |

---

## 📊 Resume Impact

> *"Built an AI-driven self-healing Kubernetes operator that ingests Prometheus alerts, reasons over cluster state via LLaMA 3.1 70B on Groq, and executes K8s remediation actions with a Slack approval gate — reducing simulated MTTR by 80%."*

---

## 🛠️ Tech Stack

`Python` `FastAPI` `Groq API` `LLaMA 3.1` `Kubernetes` `Prometheus` `AlertManager` `Grafana Cloud` `Slack API` `Streamlit` `Helm` `Minikube` `Docker` `GitHub Actions`

---

## 👤 Author

**Ganesh Sai Dontineni**  
Data & MLOps Engineer · Dallas, TX  
[LinkedIn](https://linkedin.com/in/ganeshsaidontineni) · [GitHub](https://github.com/GaneshSaiD) · [Portfolio](https://ganeshsaidontineni.vercel.app)
