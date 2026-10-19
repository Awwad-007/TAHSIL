import asyncio
from datetime import datetime

from app.channels.dispatcher import dispatch_recovery_notice
from app.guardrail_engine import evaluate
from app.llm_orchestrator import assess_anomalies, triage_agt
from app.schemas import GuardrailVerdict, InterventionType, LLMProposal, RecoveryState
from app.state_store import StateStore


def _state(**kw):
    vals = {
        "invoice_id": "inv_fraud_test",
        "customer_id": "cust_fraud_test",
        "plan_value_paise": 75000,
        "retry_count": 1,
        "last_attempt_ts": datetime(2026, 9, 4, 10, 0),
        "mandate_status": "active",
        "metadata": {},
    }
    vals.update(kw)
    return RecoveryState(**vals)


def test_anomaly_triage_forces_human_escalation():
    state = _state(metadata={
        "decline_history": [{"reason": "DECLINED"}] * 3,
        "ip_country": "in",
        "card_country": "US",
        "micro_charge_failures": 1,
    })
    flags, level, _ = assess_anomalies(state)
    assert level == "HIGH"
    assert set(flags) == {"DECLINE_FREQUENCY_HIGH", "IP_CARD_RAIL_MISMATCH", "MICRO_CHARGE_FAILURE"}
    tri = asyncio.run(triage_agt(state))
    assert tri.risk_level == "HIGH"
    assert tri.intervention == InterventionType.ESCALATE_TO_HUMAN
    verdict = evaluate(state.model_copy(update={"anomaly_flags": flags, "risk_level": level}), LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 6, 10),
        reasoning="unsafe retry",
    ))
    assert verdict.final_intervention == InterventionType.ESCALATE_TO_HUMAN


def test_channel_router_high_value_uses_whatsapp_and_email():
    state = _state()
    verdict = GuardrailVerdict(
        final_intervention=InterventionType.RETRY_AT,
        final_retry_ts=datetime(2026, 9, 6, 10),
        was_overridden=False,
        original_proposal=LLMProposal(
            intervention=InterventionType.RETRY_AT,
            proposed_retry_ts=datetime(2026, 9, 6, 10),
            reasoning="retry",
        ),
    )
    store = StateStore(":memory:")
    out = asyncio.run(dispatch_recovery_notice(state, verdict, "Your recovery notice.", store))
    assert set(out) == {"email", "whatsapp"}
    assert len([e for e in store.get_events() if e.event_type == "DISPATCH_CHANNEL_SENT"]) == 2


def test_channel_router_soft_decline_uses_email_and_sms():
    state = _state(plan_value_paise=49900)
    verdict = GuardrailVerdict(
        final_intervention=InterventionType.RETRY_AT,
        final_retry_ts=datetime(2026, 9, 6, 10),
        was_overridden=False,
        original_proposal=LLMProposal(
            intervention=InterventionType.RETRY_AT,
            proposed_retry_ts=datetime(2026, 9, 6, 10),
            reasoning="retry",
        ),
    )
    out = asyncio.run(dispatch_recovery_notice(state, verdict, "Your recovery notice.", StateStore(":memory:")))
    assert set(out) == {"email", "sms"}
