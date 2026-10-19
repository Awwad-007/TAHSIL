"""Deterministic asynchronous reconciliation of metered usage."""

from app.schemas import ContractTier, TrueUpProposal, UsageRecord


async def audit_usage_leakage(
    customer_id: str,
    raw_usage_logs: list[UsageRecord],
    contract: ContractTier,
) -> TrueUpProposal:
    """Aggregate a customer's usage and calculate its unbilled overage."""
    logs = [log for log in raw_usage_logs if log.customer_id == customer_id]
    total_units = sum(log.units_consumed for log in logs)
    allowance = contract.included_units + contract.grace_units
    unbilled_units = max(0, total_units - allowance)
    charge = unbilled_units * contract.overage_rate_paise
    reason = (
        f"Audited {len(logs)} {contract.tier_name} usage records: "
        f"{total_units} units consumed against {allowance} included units."
    )
    return TrueUpProposal(
        customer_id=customer_id,
        unbilled_units=unbilled_units,
        overage_charge_paise=charge,
        audit_reason=reason,
    )
