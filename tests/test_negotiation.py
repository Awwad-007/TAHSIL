from datetime import datetime

from fastapi.testclient import TestClient

from app import main
from app.llm_orchestrator import concession_agt, negotiator_agt
from app.schemas import CustomerReply, RecoveryState
from app.state_store import StateStore


def _state(**kwargs):
    vals = {
        "invoice_id": "inv_neg_test",
        "customer_id": "cust_neg_test",
        "plan_value_paise": 199900,
        "retry_count": 1,
        "max_retry_budget": 3,
        "last_attempt_ts": datetime(2026, 9, 4, 10, 0),
        "mandate_status": "active",
        "max_incentive_pct": 5.0,
    }
    vals.update(kwargs)
    return RecoveryState(**vals)


def test_negotiator_approves_three_day_extension():
    state = _state()
    reply = CustomerReply(
        invoice_id=state.invoice_id,
        customer_id=state.customer_id,
        message="Please give me a 3-day extension and retry my payment then.",
        received_at=datetime(2026, 9, 4, 10, 0),
    )
    res = __import__("asyncio").run(negotiator_agt(state, reply))
    assert res.requested_retry_ts == datetime(2026, 9, 7, 10, 0)
    assert res.approved is True
    assert res.verdict.final_retry_ts == datetime(2026, 9, 7, 10, 0)
    assert "Approved" in res.response


def test_negotiator_uses_cooldown_and_merchant_cap():
    state = _state(last_attempt_ts=datetime(2026, 9, 4, 10, 0))
    early = CustomerReply(
        invoice_id=state.invoice_id,
        customer_id=state.customer_id,
        message="Retry on 2026-09-04 at 14:00, please.",
        received_at=datetime(2026, 9, 4, 10, 0),
    )
    capped = CustomerReply(
        invoice_id=state.invoice_id,
        customer_id=state.customer_id,
        message="Please give me a 10% concession.",
        received_at=datetime(2026, 9, 4, 10, 0),
    )
    import asyncio
    early_res = asyncio.run(negotiator_agt(state, early))
    cap_res = asyncio.run(negotiator_agt(state, capped))
    assert early_res.approved is False
    assert early_res.verdict.final_retry_ts == datetime(2026, 9, 5, 10, 0)
    assert "24-hour" in early_res.response
    assert cap_res.approved is False
    assert cap_res.verdict.final_incentive_pct == 5.0
    assert "maximum" in cap_res.response


def test_concession_agent_unlocks_vip_discount_for_high_ltv():
    import asyncio
    assert asyncio.run(concession_agt(_state(lifetime_value_paise=150000, max_incentive_pct=10.0))) == 10.0
    assert asyncio.run(concession_agt(_state(lifetime_value_paise=75000))) == 2.0

    vip = _state(lifetime_value_paise=150000, max_incentive_pct=10.0)
    reply = CustomerReply(
        invoice_id=vip.invoice_id, customer_id=vip.customer_id,
        message="Please give me a 10% concession.",
        received_at=datetime(2026, 9, 4, 10, 0),
    )
    res = asyncio.run(negotiator_agt(vip, reply))
    assert res.approved is True
    assert res.verdict.final_incentive_pct == 10.0


def test_angry_reply_scores_high_and_logs_account_manager_escalation():
    import asyncio
    store = StateStore(":memory:")
    state = _state()
    reply = CustomerReply(
        invoice_id=state.invoice_id,
        customer_id=state.customer_id,
        message="This is ridiculous and unacceptable! I am furious and disappointed. Please give me a 3-day extension or cancel this terrible service!",
        received_at=datetime(2026, 9, 4, 10, 0),
    )
    res = asyncio.run(negotiator_agt(state, reply, store=store))
    assert res.approved is True
    assert res.churn_risk_score > 0.8
    events = store.get_events()
    alert = next(e for e in events if e.event_type == "SENTIMENT_ESCALATION")
    assert alert.payload["target"] == "internal_ops_webhook"


def test_reply_endpoint_returns_negotiation_result(monkeypatch):
    store = StateStore(":memory:")
    store.save_state(_state())
    monkeypatch.setattr(main, "default_state_store", store)
    client = TestClient(main.app)
    res = client.post("/api/webhook/reply", json={
        "invoice_id": "inv_neg_test",
        "customer_id": "cust_neg_test",
        "message": "Please give me a 3-day extension.",
        "received_at": "2026-09-04T10:00:00",
    })
    assert res.status_code == 200
    body = res.json()["data"]
    assert body["approved"] is True
    assert body["requested_retry_ts"] == "2026-09-07T10:00:00"
    assert any(e.event_type == "NEGOTIATION_EXTRACTED" for e in store.get_events())
