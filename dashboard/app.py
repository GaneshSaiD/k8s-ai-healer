# dashboard/app.py
# Streamlit dashboard for K8s AI Healer
# Shows live cluster state, incident log, pending approvals, and remediation history

import os
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="K8s AI Healer",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")

# ── Custom CSS (mirrors RAG chatbot branding) ─────────────────────────────
st.markdown("""
<style>
    /* Main background */
    .stApp { background-color: #0e1117; }

    /* Metric cards */
    [data-testid="metric-container"] {
        background: #1e2130;
        border: 1px solid #2d3150;
        border-radius: 8px;
        padding: 16px;
    }

    /* Sidebar */
    [data-testid="stSidebar"] { background-color: #161b2e; }

    /* Headers */
    h1, h2, h3 { color: #e0e0e0; }

    /* Status badges */
    .badge-critical { background:#ff4b4b; color:white; padding:3px 10px; border-radius:12px; font-size:12px; }
    .badge-warning  { background:#ffa500; color:white; padding:3px 10px; border-radius:12px; font-size:12px; }
    .badge-ok       { background:#00c853; color:white; padding:3px 10px; border-radius:12px; font-size:12px; }
    .badge-pending  { background:#2196f3; color:white; padding:3px 10px; border-radius:12px; font-size:12px; }

    /* Divider */
    hr { border-color: #2d3150; }
</style>
""", unsafe_allow_html=True)


# ── API helpers ───────────────────────────────────────────────────────────
@st.cache_data(ttl=5)
def fetch_health():
    try:
        r = requests.get(f"{WEBHOOK_URL}/health", timeout=3)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


@st.cache_data(ttl=5)
def fetch_incidents():
    try:
        r = requests.get(f"{WEBHOOK_URL}/incidents", timeout=3)
        return r.json() if r.status_code == 200 else {"total": 0, "incidents": []}
    except Exception:
        return {"total": 0, "incidents": []}


@st.cache_data(ttl=5)
def fetch_approvals(status: str = ""):
    try:
        url = f"{WEBHOOK_URL}/approvals"
        if status:
            url += f"?status={status}"
        r = requests.get(url, timeout=3)
        return r.json() if r.status_code == 200 else {"total": 0, "approvals": []}
    except Exception:
        return {"total": 0, "approvals": []}


@st.cache_data(ttl=10)
def fetch_cluster_pods(namespace: str = "demo"):
    try:
        from kubernetes import client, config
        config.load_kube_config()
        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace=namespace).items
        return [
            {
                "name":     p.metadata.name,
                "namespace": p.metadata.namespace,
                "status":   p.status.phase or "Unknown",
                "restarts": sum(
                    cs.restart_count or 0
                    for cs in (p.status.container_statuses or [])
                ),
                "node":     p.spec.node_name or "unknown",
                "age":      _pod_age(p.metadata.creation_timestamp),
            }
            for p in pods
        ]
    except Exception as e:
        return []


def _pod_age(ts) -> str:
    if not ts:
        return "unknown"
    delta = datetime.now(timezone.utc) - ts
    mins  = int(delta.total_seconds() / 60)
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h {mins % 60}m"


def approve_plan(approval_id: str) -> bool:
    try:
        r = requests.post(
            f"{WEBHOOK_URL}/approvals/{approval_id}/approve?approved_by=dashboard",
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def reject_plan(approval_id: str) -> bool:
    try:
        r = requests.post(
            f"{WEBHOOK_URL}/approvals/{approval_id}/reject?rejected_by=dashboard",
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def fire_test_alert():
    try:
        r = requests.post(f"{WEBHOOK_URL}/webhook/test", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://img.icons8.com/color/96/kubernetes.png",
        width=60,
    )
    st.title("K8s AI Healer")
    st.caption("AI-powered self-healing Kubernetes operator")
    st.divider()

    # Health check
    health = fetch_health()
    if health:
        st.success("🟢 Webhook online")
        st.caption(f"v{health.get('version', '?')} | dry_run={health.get('dry_run', True)}")
    else:
        st.error("🔴 Webhook offline")
        st.caption(f"Connecting to {WEBHOOK_URL}")

    st.divider()

    # Navigation
    page = st.radio(
        "Navigate",
        ["📊 Overview", "🚨 Pending Approvals", "📋 Incident Log", "🖥️ Cluster State"],
        label_visibility="collapsed",
    )

    st.divider()

    # Demo controls
    st.subheader("Demo Controls")
    if st.button("🔥 Fire Test Alert", use_container_width=True):
        with st.spinner("Firing test alert..."):
            result = fire_test_alert()
            if result:
                st.success("Alert fired! Check Slack.")
                st.cache_data.clear()
            else:
                st.error("Failed to fire alert")

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Sister project:")
    st.markdown("[K8s RAG Chatbot](https://k8s-rag-chatbot.streamlit.app)")
    st.caption("Built by [Ganesh Sai Dontineni](https://ganeshsaidontineni.vercel.app)")


# ── Pages ─────────────────────────────────────────────────────────────────

if page == "📊 Overview":
    st.title("📊 K8s AI Healer — Overview")
    st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

    incidents_data = fetch_incidents()
    approvals_data = fetch_approvals()
    pending_data   = fetch_approvals(status="pending")
    incidents      = incidents_data.get("incidents", [])

    # ── Top metrics ───────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Incidents", incidents_data.get("total", 0))
    with col2:
        st.metric("Pending Approvals", pending_data.get("total", 0))
    with col3:
        approved = sum(
            1 for a in approvals_data.get("approvals", [])
            if a.get("status") == "approved"
        )
        st.metric("Actions Taken", approved)
    with col4:
        firing = sum(1 for i in incidents if i.get("status") == "firing")
        st.metric("Firing Alerts", firing)

    st.divider()

    # ── Alert breakdown chart ─────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Alert Types")
        all_alerts = [
            a for inc in incidents
            for a in inc.get("alerts", [])
        ]
        if all_alerts:
            df = pd.DataFrame(all_alerts)
            counts = df["name"].value_counts().reset_index()
            counts.columns = ["Alert", "Count"]
            fig = px.bar(
                counts, x="Alert", y="Count",
                color="Count",
                color_continuous_scale="Blues",
                template="plotly_dark",
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
                margin=dict(l=0, r=0, t=20, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No alerts yet — fire a test alert to get started")

    with col_right:
        st.subheader("Action Distribution")
        all_plans = [
            p for inc in incidents
            for p in inc.get("plans", [])
        ]
        if all_plans:
            df = pd.DataFrame(all_plans)
            counts = df["action"].value_counts().reset_index()
            counts.columns = ["Action", "Count"]
            fig = px.pie(
                counts, names="Action", values="Count",
                color_discrete_sequence=px.colors.sequential.Blues_r,
                template="plotly_dark",
                hole=0.4,
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=20, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No remediation plans yet")

    # ── Recent activity ───────────────────────────────────────────────────
    st.subheader("Recent Activity")
    if incidents:
        for inc in reversed(incidents[-5:]):
            ts = inc.get("timestamp", "")[:19].replace("T", " ")
            status = inc.get("status", "unknown")
            badge = "🔴" if status == "firing" else "✅"
            alerts_str = ", ".join(
                a.get("name", "") for a in inc.get("alerts", [])
            )
            with st.expander(f"{badge} {ts} — {alerts_str}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.json(inc.get("alerts", []))
                with col2:
                    st.json(inc.get("plans", []))
    else:
        st.info("No incidents yet")


elif page == "🚨 Pending Approvals":
    st.title("🚨 Pending Approvals")
    st.caption("Review and approve/reject AI remediation plans")

    pending = fetch_approvals(status="pending").get("approvals", [])

    if not pending:
        st.success("✅ No pending approvals — cluster is healthy")
        st.info("Fire a test alert from the sidebar to generate an approval request")
    else:
        for approval in pending:
            approval_id = approval.get("approval_id", "")
            plan        = approval.get("plan", {})
            ctx         = approval.get("ctx", {})

            severity = ctx.get("severity", "unknown")
            emoji    = "🔴" if severity == "critical" else "🟡"

            with st.container():
                st.markdown(f"### {emoji} {ctx.get('alert_name', 'Unknown Alert')}")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"**Namespace:** `{ctx.get('namespace', 'N/A')}`")
                    st.markdown(f"**Pod:** `{ctx.get('pod', 'N/A')}`")
                with col2:
                    st.markdown(f"**Action:** `{plan.get('action', 'N/A')}`")
                    st.markdown(f"**Target:** `{plan.get('target', 'N/A')}`")
                with col3:
                    conf = plan.get("confidence", 0)
                    st.markdown(f"**Confidence:** {int(conf * 100)}%")
                    st.progress(conf)

                st.markdown(f"**AI Reasoning:** _{plan.get('reason', 'N/A')}_")
                st.markdown(f"**Approval ID:** `{approval_id}`")
                st.markdown(f"**Created:** {approval.get('created_at', '')[:19].replace('T', ' ')} UTC")

                col_approve, col_reject, _ = st.columns([1, 1, 4])
                with col_approve:
                    if st.button(f"✅ Approve", key=f"approve_{approval_id}",
                                 type="primary", use_container_width=True):
                        with st.spinner("Approving..."):
                            if approve_plan(approval_id):
                                st.success("Approved! Check Slack for result.")
                                st.cache_data.clear()
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("Failed to approve")
                with col_reject:
                    if st.button(f"❌ Reject", key=f"reject_{approval_id}",
                                 use_container_width=True):
                        with st.spinner("Rejecting..."):
                            if reject_plan(approval_id):
                                st.warning("Rejected.")
                                st.cache_data.clear()
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("Failed to reject")

                st.divider()


elif page == "📋 Incident Log":
    st.title("📋 Incident Log")

    incidents_data = fetch_incidents()
    incidents      = incidents_data.get("incidents", [])

    col1, col2 = st.columns([4, 1])
    with col1:
        st.caption(f"Total incidents: {incidents_data.get('total', 0)}")
    with col2:
        if st.button("🗑️ Clear Log"):
            try:
                requests.delete(f"{WEBHOOK_URL}/incidents", timeout=3)
                st.cache_data.clear()
                st.rerun()
            except Exception:
                st.error("Failed to clear")

    if not incidents:
        st.info("No incidents logged yet")
    else:
        rows = []
        for inc in reversed(incidents):
            for alert in inc.get("alerts", []):
                rows.append({
                    "Time":      inc.get("timestamp", "")[:19].replace("T", " "),
                    "Alert":     alert.get("name", ""),
                    "Severity":  alert.get("severity", "").upper(),
                    "Namespace": alert.get("namespace", ""),
                    "Pod":       alert.get("pod", ""),
                    "Action":    alert.get("action", ""),
                    "Status":    inc.get("status", "").upper(),
                })

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Severity": st.column_config.TextColumn("Severity"),
                    "Status":   st.column_config.TextColumn("Status"),
                }
            )


elif page == "🖥️ Cluster State":
    st.title("🖥️ Cluster State")
    st.caption("Live pod state from Minikube")

    namespace = st.selectbox("Namespace", ["demo", "default", "monitoring"], index=0)
    pods      = fetch_cluster_pods(namespace)

    if not pods:
        st.warning(f"No pods found in namespace `{namespace}` or cluster unreachable")
    else:
        # Summary metrics
        total    = len(pods)
        running  = sum(1 for p in pods if p["status"] == "Running")
        errored  = sum(1 for p in pods if p["status"] in ["Error", "CrashLoopBackOff"])
        pending  = sum(1 for p in pods if p["status"] == "Pending")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Pods",   total)
        col2.metric("Running",      running,  delta=running)
        col3.metric("Error/Crash",  errored,  delta=-errored if errored else 0,
                    delta_color="inverse")
        col4.metric("Pending",      pending)

        st.divider()

        # Pod table with color coding
        df = pd.DataFrame(pods)

        def color_status(val):
            colors = {
                "Running":          "color: #00c853",
                "Error":            "color: #ff4b4b",
                "CrashLoopBackOff": "color: #ff4b4b",
                "Pending":          "color: #ffa500",
                "Terminating":      "color: #9e9e9e",
            }
            return colors.get(val, "color: #e0e0e0")

        styled = df.style.map(color_status, subset=["status"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # Restart count bar chart
        if any(p["restarts"] > 0 for p in pods):
            st.subheader("Restart Counts")
            df_restarts = df[df["restarts"] > 0].sort_values("restarts", ascending=False)
            fig = px.bar(
                df_restarts, x="name", y="restarts",
                color="restarts",
                color_continuous_scale="Reds",
                template="plotly_dark",
                labels={"name": "Pod", "restarts": "Restarts"},
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=20, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)


# ── Auto-refresh ──────────────────────────────────────────────────────────
if st.sidebar.checkbox("Auto-refresh (10s)", value=False):
    time.sleep(10)
    st.cache_data.clear()
    st.rerun()
