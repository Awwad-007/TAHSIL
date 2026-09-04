from datetime import datetime, timedelta
import pytest
from app.schemas import RecoveryState, LLMProposal, GuardrailVerdict, InterventionType, MandateType
from app.guardrail_engine import evaluate
from app.state_store import StateStore

def create_base_state(**kwargs) -> RecoveryState:
    defaults = {
        "invoice_id": "inv_test_001",
        "mandate_id": "man_test_001",
        "mandate_type": MandateType.UPI_AUTOPAY,
        "customer_id": "cust_test_001",
        "plan_value_paise": 199900,
        "retry_count": 0,
        "max_retry_budget": 3,
        "last_attempt_ts": datetime(2026, 9, 4, 10, 0, 0),
        "last_failure_reason": "INSUFFICIENT_FUNDS",
        "predicted_liquidity_window_start": datetime(2026, 9, 5, 8, 0, 0),
        "predicted_liquidity_window_end": datetime(2026, 9, 5, 14, 0, 0),
        "bank_downtime_forecast": None,
        "mandate_status": "active",
        "max_incentive_pct": 5.0
    }
    defaults.update(kwargs)
    return RecoveryState(**defaults)

def test_rule1_revoked_mandate_forces_do_nothing():
    state = create_base_state(mandate_status="revoked")
    proposal = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 5, 10, 0, 0),
        reasoning="Customer usually pays on 5th"
    )
    verdict = evaluate(state, proposal)
    assert verdict.final_intervention == InterventionType.DO_NOTHING
    assert verdict.was_overridden is True
    assert "mandate not active" in verdict.override_reason

def test_rule2_retry_budget_exhausted_overridden_to_escalate():
    state = create_base_state(retry_count=3, max_retry_budget=3, last_failure_reason="U16_RISK_DECLINE")
    proposal = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 6, 10, 0, 0),
        reasoning="Attempting one more automated recovery attempt."
    )
    verdict = evaluate(state, proposal)
    assert verdict.final_intervention == InterventionType.ESCALATE_TO_HUMAN
    assert verdict.final_retry_ts is None
    assert verdict.was_overridden is True
    assert "NPCI retry budget exhausted" in verdict.override_reason

def test_rule3_cooldown_24h_clamped_forward():
    state = create_base_state(last_attempt_ts=datetime(2026, 9, 4, 10, 0, 0), retry_count=1)
    proposal = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 4, 16, 0, 0),
        reasoning="Quick retry in afternoon."
    )
    verdict = evaluate(state, proposal)
    assert verdict.final_intervention == InterventionType.RETRY_AT
    assert verdict.was_overridden is True
    expected_ts = datetime(2026, 9, 5, 10, 0, 0)
    assert verdict.final_retry_ts == expected_ts
    assert "enforced 24h NPCI cooldown" in verdict.override_reason

def test_rule4_bank_downtime_collision_shifted():
    state = create_base_state(
        last_attempt_ts=datetime(2026, 9, 4, 0, 0, 0),
        bank_downtime_forecast="2026-09-05T02:00:00 to 2026-09-05T06:00:00"
    )
    proposal = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 5, 4, 0, 0),
        reasoning="Early morning retry."
    )
    verdict = evaluate(state, proposal)
    assert verdict.final_intervention == InterventionType.RETRY_AT
    assert verdict.was_overridden is True
    expected_shifted = datetime(2026, 9, 5, 6, 15, 0)
    assert verdict.final_retry_ts == expected_shifted
    assert "bank downtime window" in verdict.override_reason

def test_rule5_incentive_ceiling_clamped():
    state = create_base_state(max_incentive_pct=5.0)
    proposal = LLMProposal(
        intervention=InterventionType.SEND_PAYMENT_LINK,
        proposed_incentive_pct=15.0,
        reasoning="Offer generous 15% discount to recover high churn risk."
    )
    verdict = evaluate(state, proposal)
    assert verdict.final_intervention == InterventionType.SEND_PAYMENT_LINK
    assert verdict.final_incentive_pct == 5.0
    assert verdict.was_overridden is True
    assert "incentive exceeded merchant cap" in verdict.override_reason

def test_rule6_idempotency_lock_blocks_duplicate():
    store = StateStore(":memory:")
    state = create_base_state(retry_count=1)
    store.acquire_lock(state.invoice_id, state.retry_count, status="in_flight")

    proposal = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 6, 10, 0, 0),
        reasoning="Standard retry."
    )
    verdict = evaluate(state, proposal, state_store=store)
    assert verdict.final_intervention == InterventionType.DO_NOTHING
    assert verdict.was_overridden is True
    assert "idempotency lock" in verdict.override_reason

def test_rule7_valid_proposal_passthrough():
    state = create_base_state(
        last_attempt_ts=datetime(2026, 9, 4, 8, 0, 0),
        retry_count=1,
        max_retry_budget=3,
        mandate_status="active",
        max_incentive_pct=5.0
    )
    proposal = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 5, 10, 0, 0),
        reasoning="Customer has highest balance at 10 AM on salary day."
    )
    verdict = evaluate(state, proposal)
    assert verdict.final_intervention == InterventionType.RETRY_AT
    assert verdict.final_retry_ts == datetime(2026, 9, 5, 10, 0, 0)
    assert verdict.was_overridden is False
    assert verdict.override_reason is None
