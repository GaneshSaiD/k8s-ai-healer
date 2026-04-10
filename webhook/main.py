# webhook/main.py
# FastAPI webhook receiver — entry point for all AlertManager alerts
# Run locally: uvicorn webhook.main:app --reload --port 8000

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from webhook.models import (
    AlertManagerPayload,
    AlertContext,
    RemediationPlan,
    WebhookResponse,
)
from webhook.alert_parser import alert_parser

# ── Logging setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── In-memory incident log (replaced by DB in production) ─────────────────
incident_log: list[dict] = []


# ── App lifecycle ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("K8s AI Healer webhook starting up...")
    logger.info(f"DRY_RUN mode: {os.getenv('DRY_RUN', 'true')}")
    logger.info(f"Watching namespaces: {os.getenv('WATCH_NAMESPACES', 'all')}")
    yield
    logger.info("K8s AI Healer webhook shutting down...")


# ── FastAPI app ───────────────────────────────────────────────────────────
app = FastAPI(
    title="K8s AI Healer",
    description="AI-powered self-healing Kubernetes operator webhook receiver",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth dependency ───────────────────────────────────────────────────────
async def verify_token(authorization: str = Header(default="")):
    """Verify Bearer token from AlertManager matches our secret."""
    expected = os.getenv("WEBHOOK_SECRET_TOKEN", "")
    if not expected:
        return  # No token configured — skip auth (local dev)

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


# ── Routes ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — used by Render, Docker, and AlertManager."""
    return {
        "status": "healthy",
        "service": "k8s-ai-healer",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": os.getenv("DRY_RUN", "true") == "true",
    }


@app.get("/")
async def root():
    return {
        "service": "K8s AI Healer",
        "version": "0.1.0",
        "endpoints": {
            "webhook":  "POST /webhook/alert",
            "health":   "GET  /health",
            "incidents": "GET /incidents",
            "docs":     "GET  /docs",
        },
        "sister_project": "https://k8s-rag-chatbot.streamlit.app",
    }


@app.post("/webhook/alert", response_model=WebhookResponse)
async def receive_alert(
    payload: AlertManagerPayload,
    _: None = Depends(verify_token),
):
    """
    Main webhook endpoint — receives AlertManager payloads.
    Parses alerts → builds context → queues for LLM reasoning (Phase 3).
    """
    logger.info(
        f"Received webhook: status={payload.status} "
        f"alerts={len(payload.alerts)} "
        f"firing={len(payload.firing_alerts)}"
    )

    # Handle resolved alerts
    if payload.status == "resolved":
        logger.info("All alerts resolved — no action needed")
        _log_incident("resolved", payload, [])
        return WebhookResponse(
            status="resolved",
            message=f"{len(payload.resolved_alerts)} alert(s) resolved",
            alert_count=len(payload.resolved_alerts),
        )

    # No firing alerts
    if not payload.firing_alerts:
        return WebhookResponse(
            status="ok",
            message="No firing alerts in payload",
            alert_count=0,
        )

    # Parse firing alerts into contexts
    contexts: list[AlertContext] = alert_parser.parse(payload)
    logger.info(f"Parsed {len(contexts)} alert context(s)")

    # Log to incident log
    _log_incident("firing", payload, contexts)

    # ── Phase 3 hook (LLM reasoning — wired in next phase) ────────────────
    plans: list[RemediationPlan] = []
    for ctx in contexts:
        logger.info(
            f"Alert ready for LLM reasoning: "
            f"{ctx.alert_name} | {ctx.severity} | "
            f"namespace={ctx.namespace} | action_hint={ctx.action_hint}"
        )
        # Phase 3 will replace this with: plans.append(await llm_engine.reason(ctx))

    return WebhookResponse(
        status="received",
        message=f"Processed {len(contexts)} firing alert(s). LLM reasoning in Phase 3.",
        alert_count=len(contexts),
        plans=plans,
    )


@app.get("/incidents")
async def get_incidents():
    """Return all incidents logged so far — used by the Streamlit dashboard."""
    return {
        "total": len(incident_log),
        "incidents": incident_log[-50:],  # Last 50
    }


@app.delete("/incidents")
async def clear_incidents():
    """Clear incident log — useful for demo resets."""
    incident_log.clear()
    return {"status": "cleared"}


@app.post("/webhook/test")
async def test_webhook():
    """
    Fire a synthetic test alert — no need for real AlertManager.
    Useful for local development and demos.
    """
    from webhook.models import Alert
    from datetime import datetime, timezone

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
                    "alertname":  "PodCrashLooping",
                    "severity":   "critical",
                    "namespace":  "demo",
                    "pod":        "crashloop-app-84988c79bd-stk9w",
                    "action":     "restart_pod",
                },
                annotations={
                    "summary":     "Pod crashloop-app-84988c79bd-stk9w is crash looping",
                    "description": "Pod has restarted more than once per minute for the last 2 minutes.",
                },
                startsAt=datetime.now(timezone.utc),
                endsAt=datetime.now(timezone.utc),
                fingerprint="test-fingerprint-001",
            )
        ],
    )

    return await receive_alert(test_payload)


# ── Internal helpers ──────────────────────────────────────────────────────

def _log_incident(
    status: str,
    payload: AlertManagerPayload,
    contexts: list[AlertContext],
):
    """Append incident to in-memory log."""
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
    })
