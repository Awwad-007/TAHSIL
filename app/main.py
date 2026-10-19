import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.schemas import CustomerReply, RecoveryState, LLMProposal, GuardrailVerdict, RecoveryEvent, normalize_gateway_payload
from app.state_store import default_state_store
from app.guardrail_engine import evaluate
from app.llm_orchestrator import assess_anomalies, negotiator_agt, propose_intervention_parallel
from app.executor import execute
from app.channels.dispatcher import dispatch_recovery_notice
from app.simulator import get_demo_scenarios, get_scenario

app = FastAPI(
    title="TAHSĪL — Autonomous AI Revenue Recovery Engine",
    description="Platform-agnostic neuro-symbolic AI engine with deterministic guardrails for subscription recovery across global payment gateways.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "tahsil-ai-revenue-recovery",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

@app.get("/dashboard")
def dashboard_redirect():
    return RedirectResponse(url="/static/dashboard.html")

@app.get("/api/events")
def get_events(limit: int = 100):
    events = default_state_store.get_events(limit=limit)
    return [e.model_dump(mode="json") for e in events]

@app.get("/api/states")
def get_states():
    states = default_state_store.list_states()
    return [s.model_dump(mode="json") for s in states]

@app.get("/api/metrics")
def get_metrics():
    return default_state_store.get_metrics()

@app.get("/api/scenarios")
def list_scenarios():
    scenarios = get_demo_scenarios()
    return {
        name: {
            "invoice_id": s.invoice_id,
            "payment_id": s.payment_id,
            "transaction_id": s.transaction_id,
            "customer_id": s.customer_id,
            "merchant_id": s.merchant_id,
            "gateway_name": s.gateway_name,
            "gateway_event": s.gateway_event,
            "mandate_type": s.mandate_type.value,
            "plan_value_inr": s.plan_value_paise / 100.0,
            "currency": s.currency,
            "retry_count": s.retry_count,
            "mandate_status": s.mandate_status,
            "last_failure_reason": s.last_failure_reason
        }
        for name, s in scenarios.items()
    }

async def process_recovery_pipeline(state: RecoveryState) -> Dict[str, Any]:
    flags, level, _score = assess_anomalies(state)
    state = state.model_copy(update={"anomaly_flags": flags, "risk_level": level})
    default_state_store.save_state(state)
    default_state_store.log_event(
        invoice_id=state.invoice_id,
        event_type="WEBHOOK_RECEIVED",
        payload=state.model_dump(mode="json")
    )
    def log_agent(_name: str, result: Any) -> None:
        default_state_store.log_agent_done(state.invoice_id, result)

    proposal, agents = await propose_intervention_parallel(state, on_done=log_agent)
    default_state_store.log_event(
        invoice_id=state.invoice_id,
        event_type="LLM_PROPOSED",
        payload={
            "proposal": proposal.model_dump(mode="json"),
            "agents": {name: result.model_dump(mode="json") for name, result in agents.items()},
        }
    )
    verdict = evaluate(state, proposal, default_state_store)
    default_state_store.log_event(
        invoice_id=state.invoice_id,
        event_type="GUARDRAIL_VERDICT",
        payload={"verdict": verdict.model_dump(mode="json")}
    )
    execution_res = execute(verdict, state, default_state_store)
    copy_text = verdict.original_proposal.customer_copy or verdict.original_proposal.reasoning
    channels = await dispatch_recovery_notice(state, verdict, copy_text, default_state_store)
    return {
        "invoice_id": state.invoice_id,
        "payment_id": state.payment_id,
        "transaction_id": state.transaction_id,
        "customer_id": state.customer_id,
        "state": state.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "agents": {name: result.model_dump(mode="json") for name, result in agents.items()},
        "verdict": verdict.model_dump(mode="json"),
        "execution": execution_res,
        "channels": channels,
    }

@app.post("/api/webhook/payment-failed")
@app.post("/api/webhook/ingest")
async def webhook_payment_failed(payload: Dict[str, Any]):
    state = normalize_gateway_payload(payload)
    result = await process_recovery_pipeline(state)
    return {"status": "success", "data": result}


@app.post("/api/webhook/reply")
async def webhook_reply(reply: CustomerReply):
    state = default_state_store.get_state(reply.invoice_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Recovery state not found for invoice")
    if state.customer_id != reply.customer_id:
        raise HTTPException(status_code=422, detail="Customer does not match invoice state")
    default_state_store.log_event(
        invoice_id=reply.invoice_id,
        event_type="CUSTOMER_REPLY_RECEIVED",
        payload=reply.model_dump(mode="json"),
    )
    res = await negotiator_agt(state, reply, store=default_state_store)
    default_state_store.log_event(
        invoice_id=reply.invoice_id,
        event_type="NEGOTIATION_EXTRACTED",
        payload={
            "requested_retry_ts": res.requested_retry_ts.isoformat() if res.requested_retry_ts else None,
            "requested_incentive_pct": res.requested_incentive_pct,
            "response": res.response,
            "approved": res.approved,
            "verdict": res.verdict.model_dump(mode="json"),
        },
    )
    return {"status": "success", "data": res.model_dump(mode="json")}


@app.post("/api/simulate/{scenario_name}")
async def simulate_scenario(scenario_name: str):
    try:
        state = get_scenario(scenario_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    result = await process_recovery_pipeline(state)
    return {"status": "success", "scenario": scenario_name, "data": result}

@app.post("/api/simulate-all")
async def simulate_all():
    scenarios = get_demo_scenarios()
    results = []
    for name, state in scenarios.items():
        res = await process_recovery_pipeline(state)
        results.append({"scenario": name, "result": res})
    return {
        "status": "success",
        "count": len(results),
        "metrics": default_state_store.get_metrics(),
        "results": results
    }

@app.post("/api/reset")
def reset_database():
    default_state_store.clear_all()
    return {"status": "success", "message": "Database reset successful"}
