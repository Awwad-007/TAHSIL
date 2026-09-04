from datetime import datetime, timedelta
from typing import Dict
from app.schemas import RecoveryState, MandateType

def get_demo_scenarios() -> Dict[str, RecoveryState]:
    now = datetime(2026, 9, 4, 10, 0, 0)
    return {
        "scenario_standard_retry": RecoveryState(
            invoice_id="inv_std_001",
            mandate_id="sub_std_upi_101",
            mandate_type=MandateType.UPI_AUTOPAY,
            customer_id="cust_rajesh_sharma",
            plan_value_paise=149900,
            retry_count=0,
            max_retry_budget=3,
            last_attempt_ts=now - timedelta(hours=26),
            last_failure_reason="U30_INSUFFICIENT_FUNDS",
            predicted_liquidity_window_start=None,
            predicted_liquidity_window_end=None,
            bank_downtime_forecast=None,
            mandate_status="active",
            max_incentive_pct=5.0
        ),
        "scenario_liquidity_window": RecoveryState(
            invoice_id="inv_liq_002",
            mandate_id="sub_liq_upi_202",
            mandate_type=MandateType.UPI_AUTOPAY,
            customer_id="cust_priya_patel",
            plan_value_paise=299900,
            retry_count=1,
            max_retry_budget=3,
            last_attempt_ts=now - timedelta(hours=12),
            last_failure_reason="U30_INSUFFICIENT_FUNDS",
            predicted_liquidity_window_start=datetime(2026, 9, 5, 8, 30, 0),
            predicted_liquidity_window_end=datetime(2026, 9, 5, 14, 0, 0),
            bank_downtime_forecast=None,
            mandate_status="active",
            max_incentive_pct=5.0
        ),
        "scenario_exhausted_budget": RecoveryState(
            invoice_id="inv_fail_003",
            mandate_id="sub_cap_upi_303",
            mandate_type=MandateType.UPI_AUTOPAY,
            customer_id="cust_vikram_singh",
            plan_value_paise=499900,
            retry_count=3,
            max_retry_budget=3,
            last_attempt_ts=now - timedelta(hours=28),
            last_failure_reason="U16_RISK_DECLINE",
            predicted_liquidity_window_start=None,
            predicted_liquidity_window_end=None,
            bank_downtime_forecast=None,
            mandate_status="active",
            max_incentive_pct=5.0
        ),
        "scenario_bank_downtime": RecoveryState(
            invoice_id="inv_down_004",
            mandate_id="sub_hdfc_card_404",
            mandate_type=MandateType.CARD,
            customer_id="cust_ananya_deshmukh",
            plan_value_paise=99900,
            retry_count=1,
            max_retry_budget=3,
            last_attempt_ts=now - timedelta(hours=24),
            last_failure_reason="INTERNAL_SYSTEM_ERROR",
            predicted_liquidity_window_start=None,
            predicted_liquidity_window_end=None,
            bank_downtime_forecast="2026-09-05T02:00:00 to 2026-09-05T06:00:00",
            mandate_status="active",
            max_incentive_pct=5.0
        ),
        "scenario_revoked_mandate": RecoveryState(
            invoice_id="inv_revk_005",
            mandate_id="sub_rev_enach_505",
            mandate_type=MandateType.ENACH,
            customer_id="cust_karan_mehta",
            plan_value_paise=199900,
            retry_count=0,
            max_retry_budget=3,
            last_attempt_ts=now - timedelta(hours=6),
            last_failure_reason="MANDATE_REVOKED_BY_CUSTOMER",
            predicted_liquidity_window_start=None,
            predicted_liquidity_window_end=None,
            bank_downtime_forecast=None,
            mandate_status="revoked",
            max_incentive_pct=5.0
        ),
        "scenario_excessive_incentive": RecoveryState(
            invoice_id="inv_inc_006",
            mandate_id="sub_inc_card_606",
            mandate_type=MandateType.CARD,
            customer_id="cust_deepak_nair",
            plan_value_paise=599900,
            retry_count=2,
            max_retry_budget=3,
            last_attempt_ts=now - timedelta(hours=30),
            last_failure_reason="PAYMENT_PROCESSING_TIMED_OUT",
            predicted_liquidity_window_start=None,
            predicted_liquidity_window_end=None,
            bank_downtime_forecast=None,
            mandate_status="active",
            max_incentive_pct=5.0
        )
    }

def get_scenario(name: str) -> RecoveryState:
    scenarios = get_demo_scenarios()
    if name in scenarios:
        return scenarios[name]
    raise ValueError(f"Unknown scenario {name}. Available: {list(scenarios.keys())}")
