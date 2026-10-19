import asyncio
from datetime import datetime

from app.guardrail_engine import evaluate_true_up
from app.reconcile_agt import audit_usage_leakage
from app.schemas import ContractTier, UsageRecord
from app.state_store import StateStore


def _contract(**overrides):
    values = {
        "tier_name": "pro",
        "base_fee_paise": 10_000,
        "included_units": 1_000,
        "overage_rate_paise": 25,
        "grace_units": 100,
    }
    values.update(overrides)
    return ContractTier(**values)


def _logs(units):
    return [UsageRecord(
        customer_id="cust_usage_001",
        resource_name="api_calls",
        units_consumed=units,
        recorded_at=datetime(2026, 10, 1),
    )]


def test_standard_usage_reconciliation():
    proposal = asyncio.run(audit_usage_leakage("cust_usage_001", _logs(1_250), _contract()))
    assert proposal.unbilled_units == 150
    assert proposal.overage_charge_paise == 3_750
    assert proposal.customer_id == "cust_usage_001"


def test_rule8_clamps_true_up_surge_to_150_percent_of_base_fee():
    store = StateStore(":memory:")
    contract = _contract(base_fee_paise=10_000, overage_rate_paise=100)
    proposal = asyncio.run(audit_usage_leakage("cust_usage_001", _logs(2_000), contract))
    verdict = evaluate_true_up(proposal, contract, "2026-10", store=store)
    assert proposal.overage_charge_paise == 90_000
    assert verdict.approved_charge_paise == 15_000
    assert verdict.was_clamped is True
    assert "150%" in verdict.clamp_reason


def test_rule9_blocks_second_true_up_invoice_in_same_cycle():
    store = StateStore(":memory:")
    contract = _contract()
    proposal = asyncio.run(audit_usage_leakage("cust_usage_001", _logs(1_250), contract))
    first = evaluate_true_up(proposal, contract, "2026-10", store=store)
    store.log_true_up_audited(proposal, cycle_id="2026-10")
    store.log_true_up_invoice_generated(proposal, first.model_dump(mode="json"), cycle_id="2026-10")

    second = evaluate_true_up(proposal, contract, "2026-10", store=store)
    assert second.approved_charge_paise == 0
    assert second.was_clamped is True
    assert "already audited" in second.clamp_reason
