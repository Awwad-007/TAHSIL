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
        "payment_id": state.payment_id,
        "transaction_id": state.transaction_id,
        "customer_id": state.customer_id,
        "merchant_id": state.merchant_id,
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
        target_id = state.transaction_id or state.mandate_id or state.payment_id or state.invoice_id
        payload = {
            "gateway": state.gateway_name or "universal",
            "endpoint": f"/v1/payments/{target_id}/retry",
            "method": "POST",
            "body": {
                "scheduled_at": scheduled_ts,
                "retry_count": state.retry_count + 1,
                "mandate_type": state.mandate_type.value,
                "invoice_id": state.invoice_id,
                "payment_id": state.payment_id,
                "transaction_id": target_id,
                "customer_id": state.customer_id,
                "merchant_id": state.merchant_id,
                "amount": state.plan_value_paise,
                "currency": state.currency
            }
        }
        execution_result["gateway_call"] = payload
        execution_result["razorpay_call"] = payload  # Backward-compatible alias
        print(f"[MOCK EXECUTION] Target Endpoint: POST /v1/payments/{target_id}/retry")
        print(f"[MOCK EXECUTION] Scheduled Retry Time: {scheduled_ts}")
        print(f"[MOCK EXECUTION] Payload: {json.dumps(payload['body'], indent=2)}")

    elif intervention == InterventionType.SEND_PAYMENT_LINK:
        incentive = verdict.final_incentive_pct or 0.0
        discount_factor = (100.0 - incentive) / 100.0
        effective_amount = int(state.plan_value_paise * discount_factor)
        payment_link_url = f"https://pay.tahsil.ai/l/mock_{state.invoice_id[:8]}"
        
        customer_msg = verdict.original_proposal.customer_copy or (
            f"Dear Customer, we noticed a temporary issue processing your subscription payment for invoice {state.invoice_id}. "
            f"To keep your account active without interruption, we have generated a secure direct payment link with an exclusive "
            f"{incentive}% courtesy discount: {payment_link_url}. Thank you for your continued partnership."
            if incentive > 0 else
            f"Dear Customer, we noticed a temporary issue processing your subscription payment for invoice {state.invoice_id}. "
            f"Please complete your renewal via this secure direct link to prevent service interruption: {payment_link_url}. "
            f"Thank you for choosing us."
        )

        payload = {
            "gateway": state.gateway_name or "universal",
            "endpoint": "/v1/payment_links",
            "method": "POST",
            "body": {
                "amount": effective_amount,
                "currency": state.currency,
                "description": f"Payment recovery for Invoice {state.invoice_id} (Includes {incentive}% recovery incentive)",
                "customer": {
                    "id": state.customer_id,
                },
                "notify": {"sms": True, "email": True, "whatsapp": True},
                "reminder_enable": True,
                "reference_id": state.invoice_id,
                "customer_recovery_message": customer_msg
            }
        }
        execution_result["gateway_call"] = payload
        execution_result["razorpay_call"] = payload  # Backward-compatible alias
        execution_result["payment_link_url"] = payment_link_url
        execution_result["customer_recovery_message"] = customer_msg
        print("[MOCK EXECUTION] Target Endpoint: POST /v1/payment_links")
        curr_symbol = "₹" if state.currency.upper() == "INR" else f"{state.currency.upper()} "
        print(f"[MOCK EXECUTION] Original Amount: {curr_symbol}{state.plan_value_paise / 100:.2f} | Discounted Amount: {curr_symbol}{effective_amount / 100:.2f} ({incentive}% off)")
        print(f"[MOCK EXECUTION] Generated Link: {payment_link_url}")
        print(f"[MOCK EXECUTION] Empathetic Recovery Message: \"{customer_msg}\"")

    elif intervention == InterventionType.PAUSE_MANDATE:
        target_id = state.transaction_id or state.mandate_id or state.payment_id or state.invoice_id
        payload = {
            "gateway": state.gateway_name or "universal",
            "endpoint": f"/v1/subscriptions/{target_id}/pause",
            "method": "POST",
            "body": {
                "pause_at": "now",
                "customer_id": state.customer_id,
                "merchant_id": state.merchant_id,
                "reason": "Repeated automated debit failure prevention"
            }
        }
        execution_result["gateway_call"] = payload
        execution_result["razorpay_call"] = payload  # Backward-compatible alias
        print(f"[MOCK EXECUTION] Target Endpoint: POST /v1/subscriptions/{target_id}/pause")
        print(f"[MOCK EXECUTION] Subscription {target_id} status set to PAUSED.")

    elif intervention == InterventionType.ESCALATE_TO_HUMAN:
        payload = {
            "target": "internal_ops_webhook",
            "method": "POST",
            "body": {
                "alert": "REVENUE_RECOVERY_ESCALATION",
                "severity": "HIGH",
                "invoice_id": state.invoice_id,
                "customer_id": state.customer_id,
                "merchant_id": state.merchant_id,
                "retry_count": state.retry_count,
                "plan_value": state.plan_value_paise / 100.0,
                "currency": state.currency,
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

