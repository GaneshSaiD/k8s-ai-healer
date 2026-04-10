# webhook/main.py
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware

from webhook.models import (
    AlertManagerPayload,
    AlertContext,
    RemediationPlan,
    WebhookResponse,
)
from webhook.alert_parser import alert_parser

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

incident_log: list[dict] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("K8s AI Healer webhook starting up...")
    logger.info(f"DRY_RUN mode: {os.getenv('DRY_RUN', 'true')}")
    yield
    logger.info("K8s AI Healer webhook shutting down...")


app = FastAPI(
    title="K8s AI Healer",
    description="AI-powered self-healing Kubernetes operator webhook receiver",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_token(authorization: str = Header(default="")):
    expected = os.getenv("WEBHOOK_SECRET_TOKEN", "")
    if not expected:
        return
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@app.get("/health")
async def health():
    return {
        "status":    "healthy",
        "service":   "k8s-ai-healer",
        "version":   "0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run":   os.getenv("DRY_RUN", "true") == "true",
    }


@app.get("/")
async def root():
    return {
        "service": "K8s AI Healer",
        "version": "0.2.0",
        "endpoints": {
            "webhook":   "POST /webhook/alert",
            "health":    "GET  /health",
            "incidents": "GET  /incidents",
            "docs":      "GET  /docs",
        },
        "sister_project": "https://k8s-rag-chatbot.streamlit.app",
    }


@app.post("/webhook/alert", response_model=WebhookResponse)
async def receive_alert(
    payload: AlertManagerPayload,
    _: None = Depends(verify_token),
):
    from llm.action_planner import action_planner

    logger.info(
        f"Received webhook: status={payload.status} "
        f"alerts={len(payload.alerts)} "
        f"firing={len(payload.firing_alerts)}"
    )

    if payload.status == "resolved":
        _log_incident("resolved", payload, [], [])
        return WebhookResponse(
            status="resolved",
            message=f"{len(payload.resolved_alerts)} alert(s) resolved",
            alert_count=len(payload.resolved_alerts),
        )

    if not payload.firing_alerts:
        return WebhookResponse(status="ok", message="No firing alerts", alert_count=0)

    contexts: list[AlertContext] = alert_parser.parse(payload)

    plans: list[RemediationPlan] = []
    for ctx in contexts:
        try:
            plan = await action_planner.plan(ctx)
            plans.append(plan)
            logger.info(
                f"Plan: {plan.action} on {plan.target} | "
                f"confidence={plan.confidence:.0%} | approved={plan.approved}"
            )
        except Exception as e:
            logger.error(f"Planning failed for {ctx.alert_name}: {e}")

    _log_incident("firing", payload, contexts, plans)

    for plan in plans:
        if not plan.approved:
            logger.info(f"Awaiting Slack approval: {plan.action} on {plan.target}")

    return WebhookResponse(
        status="planned",
        message=f"Generated {len(plans)} remediation plan(s)",
        alert_count=len(contexts),
        plans=plans,
    )


@app.get("/incidents")
async def get_incidents():
    return {"total": len(incident_log), "incidents": incident_log[-50:]}


@app.delete("/incidents")
async def clear_incidents():
    incident_log.clear()
    return {"status": "cleared"}


@app.post("/webhook/test")
async def test_webhook():
    from webhook.models import Alert

    test_payload = AlertManagerPayload(
        version="4",
        status="firing",
        receiver="k8s-ai-healer-webhook",
        groupLabels={"alertname": "PodCrashLooping"},
        commonLabels={"severity": "critical"},
        commonAnnotations={},
        alerts=[
            Alert(
                status="firing",
                labels={
                    "alertname": "PodCrashLooping",
                    "severity":  "critical",
                    "namespace": "demo",
                    "pod":       "crashloop-app-84988c79bd-stk9w",
                    "action":    "restart_pod",
                },
                annotations={
                    "summary":     "Pod crashloop-app-84988c79bd-stk9w is crash looping",
                    "description": "Pod has restarted more than once per minute for 2 minutes.",
                },
                startsAt=datetime.now(timezone.utc),
                endsAt=datetime.now(timezone.utc),
                fingerprint="test-fingerprint-001",
            )
        ],
    )
    return await receive_alert(test_payload)


def _log_incident(
    status: str,
    payload: AlertManagerPayload,
    contexts: list[AlertContext],
    plans: list[RemediationPlan],
):
    incident_log.append({
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "status":      status,
        "alert_count": len(payload.alerts),
        "alerts": [
            {
                "name":      ctx.alert_name,
                "severity":  ctx.severity,
                "namespace": ctx.namespace,
                "pod":       ctx.pod,
                "action":    ctx.action_hint,
                "summary":   ctx.summary,
            }
            for ctx in contexts
        ],
        "plans": [
            {
                "action":     p.action,
                "target":     p.target,
                "namespace":  p.namespace,
                "confidence": p.confidence,
                "approved":   p.approved,
                "reason":     p.reason,
            }
            for p in plans
        ],
    })