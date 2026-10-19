import asyncio
from typing import Any, Dict, Optional

from app.schemas import GuardrailVerdict, InterventionType, RecoveryState
from app.state_store import StateStore, default_state_store


async def _email(state: RecoveryState, text: str) -> Dict[str, str]:
    await asyncio.sleep(0)
    return {"channel": "email", "provider": "mock_smtp_resend", "status": "MOCKED_SUCCESS", "text": text}


async def _sms(state: RecoveryState, text: str) -> Dict[str, str]:
    await asyncio.sleep(0)
    return {"channel": "sms", "provider": "mock_twilio", "status": "MOCKED_SUCCESS", "text": text}


async def _whatsapp(state: RecoveryState, text: str) -> Dict[str, str]:
    await asyncio.sleep(0)
    return {"channel": "whatsapp", "provider": "mock_meta_business_api", "status": "MOCKED_SUCCESS", "text": text}


async def dispatch_recovery_notice(
    state: RecoveryState,
    verdict: GuardrailVerdict,
    copy_text: str,
    store: Optional[StateStore] = None,
) -> Dict[str, Dict[str, str]]:
    if verdict.final_intervention == InterventionType.DO_NOTHING:
        return {}
    db = store or default_state_store
    high_value = state.plan_value_paise > 50000
    chans = ["whatsapp", "email"] if high_value else ["email", "sms"]
    fns = {"email": _email, "sms": _sms, "whatsapp": _whatsapp}
    vals = await asyncio.gather(*(fns[ch](state, copy_text) for ch in chans))
    out = {}
    for val in vals:
        out[val["channel"]] = val
        db.log_dispatch_channel(
            invoice_id=state.invoice_id,
            payload={
                "channel": val["channel"],
                "provider": val["provider"],
                "status": val["status"],
                "risk_level": state.risk_level,
                "anomaly_flags": state.anomaly_flags,
            },
        )
    return out
