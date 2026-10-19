"""Deterministic asynchronous reconciliation of metered usage."""

import asyncio

from app.schemas import ContractTier, TrueUpProposal, UsageRecord


async def upsell_agt(customer_id: str, surplus: int, contract: ContractTier) -> tuple[bool, str | None]:
    """Offer a higher tier when usage materially exceeds the allowance."""
    if surplus <= contract.included_units * 0.30:
        return False, None
    copy = (
        f"Hi {customer_id}, your usage exceeded the {contract.tier_name} allowance by "
        f"{surplus:,} units. Upgrade to a higher tier and we’ll waive your current "
        "overage fee."
    )
    return True, copy


async def _charge_agt(unbilled_units: int, contract: ContractTier) -> int:
    return unbilled_units * contract.overage_rate_paise


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
    (upsell, copy), charge = await asyncio.gather(
        upsell_agt(customer_id, unbilled_units, contract),
        _charge_agt(unbilled_units, contract),
    )
    reason = (
        f"Audited {len(logs)} {contract.tier_name} usage records: "
        f"{total_units} units consumed against {allowance} included units."
    )
    return TrueUpProposal(
        customer_id=customer_id,
            unbilled_units=unbilled_units,
            overage_charge_paise=charge,
            audit_reason=reason,
            upsell_proposed=upsell,
            upsell_copy=copy,
    )
