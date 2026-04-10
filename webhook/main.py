# webhook/main.py
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

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


app = FastAPI(title="K8s AI Healer", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


async def verify_token(authorization: str = Header(default="")):
    expected = os.getenv("WEBHOOK_SECRET_TOKEN", "")
    if not expected:
        return
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "k8s-ai-healer", "version": "0.3.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run": os.getenv("DRY_RUN", "true") == "true"}


@app.get("/")
async def root():
    return {"service": "K8s AI Healer", "version": "0.3.0",
            "sister_project": "https://k8s-rag-chatbot.streamlit.app"}


@app.post("/webhook/alert", response_model=WebhookResponse)
async def receive_alert(payload: AlertManagerPayload, _: None = Depends(verify_token)):
    from llm.action_planner import action_planner
    from approvals.slack_notifier import slack_notifier
    from approvals.approval_handler import approval_handler

    logger.info(f"Webhook received: status={payload.status} firing={len(payload.firing_alerts)}")

    if payload.status == "resolved":
        _log_incident("resolved", payload, [], [])
        return WebhookResponse(status="resolved",
                               message=f"{len(payload.resolved_alerts)} resolved",
                               alert_count=len(payload.resolved_alerts))

    if not payload.firing_alerts:
        return WebhookResponse(status="ok", message="No firing alerts", alert_count=0)

    contexts: list[AlertContext] = alert_parser.parse(payload)
    plans: list[RemediationPlan] = []

    for ctx in contexts:
        try:
            plan = await action_planner.plan(ctx)
            plans.append(plan)
            if plan.action == "investigate":
                await slack_notifier.send_alert_notification(ctx)
            else:
                approval_id = approval_handler.create_approval(ctx, plan)
                await slack_notifier.send_approval_request(ctx, plan, approval_id)
                logger.info(f"Slack approval sent: {approval_id}")
        except Exception as e:
            logger.error(f"Planning failed for {ctx.alert_name}: {e}")

    _log_incident("firing", payload, contexts, plans)
    return WebhookResponse(status="planned",
                           message=f"{len(plans)} plan(s) — awaiting Slack approval",
                           alert_count=len(contexts), plans=plans)


@app.get("/approvals")
async def list_approvals(status: str = ""):
    from approvals.approval_handler import approval_handler
    approvals = approval_handler.get_all(status=status or None)
    return {"total": len(approvals), "approvals": approvals}


@app.get("/approvals/pending")
async def list_pending():
    from approvals.approval_handler import approval_handler
    pending = approval_handler.get_pending()
    return {"total": len(pending), "approvals": pending}


@app.post("/approvals/{approval_id}/approve")
async def approve_plan(approval_id: str, approved_by: str = "operator"):
    from approvals.approval_handler import approval_handler
    result = await approval_handler.approve(approval_id, approved_by)
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return result


@app.post("/approvals/{approval_id}/reject")
async def reject_plan(approval_id: str, rejected_by: str = "operator",
                      reason: str = "Rejected by operator"):
    from approvals.approval_handler import approval_handler
    result = await approval_handler.reject(approval_id, rejected_by, reason)
    if not result:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return result


@app.post("/execute")
async def execute_plan(plan: RemediationPlan, _: None = Depends(verify_token)):
    from executor.actions import k8s_actions
    if not plan.approved:
        raise HTTPException(status_code=403, detail="Plan not approved")
    result = await k8s_actions.execute(plan)
    return result.to_dict()


@app.post("/simulate")
async def simulate_plan(plan: RemediationPlan, _: None = Depends(verify_token)):
    from executor.dry_run import dry_run_validator
    return await dry_run_validator.simulate(plan)


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
        version="4", status="firing", receiver="k8s-ai-healer-webhook",
        groupLabels={"alertname": "PodCrashLooping"},
        commonLabels={"severity": "critical"}, commonAnnotations={},
        alerts=[Alert(
            status="firing",
            labels={"alertname": "PodCrashLooping", "severity": "critical",
                    "namespace": "demo", "pod": "crashloop-app-84988c79bd-stk9w",
                    "action": "restart_pod"},
            annotations={"summary": "Pod crashloop-app-84988c79bd-stk9w is crash looping",
                          "description": "Pod has restarted more than once per minute for 2 minutes."},
            startsAt=datetime.now(timezone.utc), endsAt=datetime.now(timezone.utc),
            fingerprint="test-fingerprint-001",
        )]
    )
    return await receive_alert(test_payload)


def _log_incident(status, payload, contexts, plans):
    incident_log.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status, "alert_count": len(payload.alerts),
        "alerts": [{"name": c.alert_name, "severity": c.severity,
                    "namespace": c.namespace, "pod": c.pod,
                    "action": c.action_hint, "summary": c.summary} for c in contexts],
        "plans": [{"action": p.action, "target": p.target, "namespace": p.namespace,
                   "confidence": p.confidence, "approved": p.approved,
                   "reason": p.reason} for p in plans],
    })
