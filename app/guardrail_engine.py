from datetime import datetime, timedelta
import re
from typing import Optional, Tuple
from app.schemas import RecoveryState, LLMProposal, GuardrailVerdict, InterventionType, TrueUpProposal, TrueUpVerdict, ContractTier

def parse_downtime_window(forecast_str: Optional[str]) -> Optional[Tuple[datetime, datetime]]:
    if not forecast_str:
        return None
    pattern = r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)\s*(?:to|-)\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)"
    match = re.search(pattern, forecast_str)
    if match:
        try:
            start_dt = datetime.fromisoformat(match.group(1).replace(" ", "T"))
            end_dt = datetime.fromisoformat(match.group(2).replace(" ", "T"))
            return start_dt, end_dt
        except Exception:
            pass
    return None

def evaluate(state: RecoveryState, proposal: Optional[LLMProposal] = None, state_store: Optional[object] = None, store: Optional[object] = None) -> GuardrailVerdict:
    if proposal is None:
        raise TypeError("a recovery proposal is required")
    db_store = store or state_store
    # Rule 1: Mandate validity
    if state.mandate_status != "active":
        return GuardrailVerdict(
            final_intervention=InterventionType.DO_NOTHING,
            final_retry_ts=None,
            final_incentive_pct=None,
            was_overridden=True,
            override_reason=f"mandate not active (status: {state.mandate_status})",
            original_proposal=proposal
        )

    # Rule 6: Idempotency lock
    if db_store is not None and hasattr(db_store, "is_locked"):
        if db_store.is_locked(state.invoice_id, state.retry_count):
            return GuardrailVerdict(
                final_intervention=InterventionType.DO_NOTHING,
                final_retry_ts=None,
                final_incentive_pct=None,
                was_overridden=True,
                override_reason="duplicate action blocked by idempotency lock",
                original_proposal=proposal
            )

    # Fraud/anomaly firewall: high-risk cases never enter automated recovery.
    if state.risk_level == "HIGH":
        flags = ", ".join(state.anomaly_flags) or "unclassified anomaly"
        return GuardrailVerdict(
            final_intervention=InterventionType.ESCALATE_TO_HUMAN,
            final_retry_ts=None,
            final_incentive_pct=None,
            was_overridden=True,
            override_reason=f"high-risk anomaly escalation: {flags}",
            original_proposal=proposal,
        )

    # Rule 2: Retry budget cap
    if proposal.intervention == InterventionType.RETRY_AT:
        if state.retry_count >= state.max_retry_budget:
            return GuardrailVerdict(
                final_intervention=InterventionType.ESCALATE_TO_HUMAN,
                final_retry_ts=None,
                final_incentive_pct=None,
                was_overridden=True,
                override_reason=f"Network retry budget exhausted (attempted {state.retry_count} of max {state.max_retry_budget})",
                original_proposal=proposal
            )

    # Rule 3: Minimum 24h gap between retries
    final_retry_ts = proposal.proposed_retry_ts
    was_overridden_cooldown = False
    cooldown_reason = None

    if proposal.intervention == InterventionType.RETRY_AT and state.last_attempt_ts and proposal.proposed_retry_ts:
        min_allowed_ts = state.last_attempt_ts + timedelta(hours=24)
        if proposal.proposed_retry_ts < min_allowed_ts:
            final_retry_ts = min_allowed_ts
            was_overridden_cooldown = True
            cooldown_reason = "enforced 24h network cooldown"


    # Rule 4: Bank downtime collision
    was_overridden_downtime = False
    downtime_reason = None
    if proposal.intervention == InterventionType.RETRY_AT and final_retry_ts and state.bank_downtime_forecast:
        downtime_range = parse_downtime_window(state.bank_downtime_forecast)
        if downtime_range:
            dt_start, dt_end = downtime_range
            if dt_start <= final_retry_ts <= dt_end:
                final_retry_ts = dt_end + timedelta(minutes=15)
                was_overridden_downtime = True
                downtime_reason = f"retry shifted past bank downtime window ({state.bank_downtime_forecast})"

    if was_overridden_downtime or was_overridden_cooldown:
        reasons = [r for r in [cooldown_reason, downtime_reason] if r]
        return GuardrailVerdict(
            final_intervention=InterventionType.RETRY_AT,
            final_retry_ts=final_retry_ts,
            final_incentive_pct=proposal.proposed_incentive_pct,
            was_overridden=True,
            override_reason="; ".join(reasons),
            original_proposal=proposal
        )

    # Rule 5: Incentive ceiling
    if proposal.intervention == InterventionType.SEND_PAYMENT_LINK:
        proposed_inc = proposal.proposed_incentive_pct or 0.0
        if proposed_inc > state.max_incentive_pct:
            return GuardrailVerdict(
                final_intervention=InterventionType.SEND_PAYMENT_LINK,
                final_retry_ts=None,
                final_incentive_pct=state.max_incentive_pct,
                was_overridden=True,
                override_reason="incentive exceeded merchant cap",
                original_proposal=proposal
            )

    # Rule 7: Pass-through
    return GuardrailVerdict(
        final_intervention=proposal.intervention,
        final_retry_ts=proposal.proposed_retry_ts,
        final_incentive_pct=proposal.proposed_incentive_pct,
        was_overridden=False,
        override_reason=None,
        original_proposal=proposal
    )

evaluate_proposal = evaluate


def evaluate_true_up(proposal: TrueUpProposal, contract: ContractTier, cycle_id: Optional[str] = None, state_store: Optional[object] = None, store: Optional[object] = None) -> TrueUpVerdict:
    """Apply Rule 10, then the true-up ceiling and cycle idempotency rules."""
    cycle_id = cycle_id or datetime.now().strftime("%Y-%m")
    db_store = store or state_store
    if proposal.usage_velocity_alert:
        return TrueUpVerdict(
            customer_id=proposal.customer_id, approved_charge_paise=0,
            unbilled_units=proposal.unbilled_units, was_clamped=True,
            clamp_reason="EMERGENCY_FREEZE_ESCALATE_TO_HUMAN",
            upsell_proposed=proposal.upsell_proposed,
            upsell_copy=proposal.upsell_copy,
        )
    if db_store is not None and hasattr(db_store, "is_true_up_audited"):
        if db_store.is_true_up_audited(proposal.customer_id, cycle_id):
            return TrueUpVerdict(
                customer_id=proposal.customer_id, approved_charge_paise=0,
                unbilled_units=proposal.unbilled_units, was_clamped=True,
                clamp_reason="true-up already audited for current billing cycle",
                upsell_proposed=proposal.upsell_proposed,
                upsell_copy=proposal.upsell_copy,
            )

    final_amount = proposal.overage_charge_paise
    reason = None
    was_clamped = False
    ceiling = contract.base_fee_paise * 3 // 2
    if final_amount > ceiling:
        final_amount = ceiling
        was_clamped = True
        reason = "true-up charge exceeded 150% of the contract base fee"

    if db_store is not None and hasattr(db_store, "acquire_true_up_cycle"):
        if not db_store.acquire_true_up_cycle(proposal.customer_id, cycle_id):
            return TrueUpVerdict(
                customer_id=proposal.customer_id, approved_charge_paise=0,
                unbilled_units=proposal.unbilled_units, was_clamped=True,
                clamp_reason="true-up already audited for current billing cycle",
                upsell_proposed=proposal.upsell_proposed,
                upsell_copy=proposal.upsell_copy,
            )
    return TrueUpVerdict(
        customer_id=proposal.customer_id, approved_charge_paise=final_amount,
        unbilled_units=proposal.unbilled_units, was_clamped=was_clamped,
        clamp_reason=reason,
        upsell_proposed=proposal.upsell_proposed,
        upsell_copy=proposal.upsell_copy,
    )

