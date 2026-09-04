import json
from datetime import datetime
from typing import Optional, Dict, Any
from app.schemas import RecoveryState, GuardrailVerdict, InterventionType, RecoveryEvent
from app.state_store import StateStore, default_state_store

def execute(verdict: GuardrailVerdict, state: RecoveryState, store: Optional[StateStore] = None) -> Dict[str, Any]:
    db = store or default_state_store
    intervention = verdict.final_intervention
    timestamp = datetime.now().isoformat()
    
    execution_result: Dict[str, Any] = {
        "invoice_id": state.invoice_id,
        "intervention": intervention.value,
        "executed_at": timestamp,
        "status": "MOCKED_SUCCESS",
    }

    print("\n" + "="*60)
    print(f"[MOCK EXECUTION] Triggered action for Invoice: {state.invoice_id}")
    print(f"[MOCK EXECUTION] Final Intervention: {intervention.value}")
    
    if verdict.was_overridden:
        print(f"[MOCK EXECUTION] Guardrail Override: YES - Reason: {verdict.override_reason}")
    
    if intervention == InterventionType.RETRY_AT:
        scheduled_ts = verdict.final_retry_ts.isoformat() if verdict.final_retry_ts else timestamp
        payload = {
            "endpoint": f"https://api.razorpay.com/v1/subscriptions/{state.mandate_id}/retry",
            "method": "POST",
            "body": {
                "scheduled_at": scheduled_ts,
                "retry_count": state.retry_count + 1,
                "mandate_type": state.mandate_type.value,
                "invoice_id": state.invoice_id,
                "amount_paise": state.plan_value_paise
            }
        }
        execution_result["razorpay_call"] = payload
        print(f"[MOCK EXECUTION] Target Endpoint: POST /v1/subscriptions/{state.mandate_id}/retry")
        print(f"[MOCK EXECUTION] Scheduled Retry Time: {scheduled_ts}")
        print(f"[MOCK EXECUTION] Payload: {json.dumps(payload['body'], indent=2)}")

    elif intervention == InterventionType.SEND_PAYMENT_LINK:
        incentive = verdict.final_incentive_pct or 0.0
        discount_factor = (100.0 - incentive) / 100.0
        effective_amount = int(state.plan_value_paise * discount_factor)
        payload = {
            "endpoint": "https://api.razorpay.com/v1/payment_links",
            "method": "POST",
            "body": {
                "amount": effective_amount,
                "currency": "INR",
                "description": f"Payment link for Invoice {state.invoice_id} (Includes {incentive}% recovery incentive)",
                "customer": {
                    "id": state.customer_id,
                },
                "notify": {"sms": True, "email": True},
                "reminder_enable": True,
                "reference_id": state.invoice_id
            }
        }
        execution_result["razorpay_call"] = payload
        execution_result["payment_link_url"] = f"https://rzp.io/i/mock_{state.invoice_id[:8]}"
        print("[MOCK EXECUTION] Target Endpoint: POST /v1/payment_links")
        print(f"[MOCK EXECUTION] Original Amount: ₹{state.plan_value_paise / 100:.2f} | Discounted Amount: ₹{effective_amount / 100:.2f} ({incentive}% off)")
        print(f"[MOCK EXECUTION] Generated Link: {execution_result['payment_link_url']}")

    elif intervention == InterventionType.PAUSE_MANDATE:
        payload = {
            "endpoint": f"https://api.razorpay.com/v1/subscriptions/{state.mandate_id}/pause",
            "method": "POST",
            "body": {
                "pause_at": "now",
                "reason": "Repeated automated debit failure prevention"
            }
        }
        execution_result["razorpay_call"] = payload
        print(f"[MOCK EXECUTION] Target Endpoint: POST /v1/subscriptions/{state.mandate_id}/pause")
        print(f"[MOCK EXECUTION] Mandate {state.mandate_id} status set to PAUSED.")

    elif intervention == InterventionType.ESCALATE_TO_HUMAN:
        payload = {
            "target": "internal_ops_webhook",
            "method": "POST",
            "body": {
                "alert": "REVENUE_RECOVERY_ESCALATION",
                "severity": "HIGH",
                "invoice_id": state.invoice_id,
                "customer_id": state.customer_id,
                "retry_count": state.retry_count,
                "plan_value_inr": state.plan_value_paise / 100.0,
                "reason": verdict.override_reason or "Exhausted automated recovery budget"
            }
        }
        execution_result["ops_alert"] = payload
        print("[MOCK EXECUTION] Target: Internal Operations Escalation Queue / Slack Alert")
        print(f"[MOCK EXECUTION] Reason: {payload['body']['reason']}")
        print("[MOCK EXECUTION] Ticket generated for Customer Ops Specialist.")

    elif intervention == InterventionType.DO_NOTHING:
        execution_result["action_taken"] = "NO_OP"
        print("[MOCK EXECUTION] Action: NO_OP. No external requests dispatched.")
        print(f"[MOCK EXECUTION] Context: {verdict.override_reason or 'No intervention necessary'}")

    print("="*60 + "\n")

    db.log_event(
        invoice_id=state.invoice_id,
        event_type="EXECUTION_MOCKED",
        payload={
            "verdict": verdict.model_dump(mode="json"),
            "execution_result": execution_result
        }
    )

    return execution_result
