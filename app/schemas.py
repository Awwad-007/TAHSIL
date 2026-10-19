from pydantic import BaseModel, Field, model_validator
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any, Union, List

class MandateType(str, Enum):
    UPI_AUTOPAY = "upi_autopay"
    ENACH = "enach"
    CARD = "card"
    ACH = "ach"
    SEPA = "sepa"
    DIRECT_DEBIT = "direct_debit"

class InterventionType(str, Enum):
    RETRY_AT = "RETRY_AT"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    PAUSE_MANDATE = "PAUSE_MANDATE"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    DO_NOTHING = "DO_NOTHING"

class RecoveryState(BaseModel):
    invoice_id: str
    mandate_id: Optional[str] = None
    payment_id: Optional[str] = None
    transaction_id: Optional[str] = None
    customer_id: str
    merchant_id: Optional[str] = "merch_default"
    mandate_type: MandateType = MandateType.CARD
    plan_value_paise: int = 0
    currency: str = "INR"
    retry_count: int = 0
    max_retry_budget: int = 3
    last_attempt_ts: Optional[datetime] = None
    last_failure_reason: Optional[str] = None
    gateway_event: Optional[str] = None
    gateway_name: Optional[str] = "universal"
    predicted_liquidity_window_start: Optional[datetime] = None
    predicted_liquidity_window_end: Optional[datetime] = None
    bank_downtime_forecast: Optional[str] = None
    mandate_status: str = "active"
    max_incentive_pct: float = 5.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_identifier_fallbacks(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Ensure mandate_id, payment_id, transaction_id have fallbacks among each other
            primary_id = (
                values.get("mandate_id")
                or values.get("transaction_id")
                or values.get("payment_id")
                or values.get("invoice_id")
                or "txn_default"
            )
            if not values.get("mandate_id"):
                values["mandate_id"] = primary_id
            if not values.get("payment_id"):
                values["payment_id"] = primary_id
            if not values.get("transaction_id"):
                values["transaction_id"] = primary_id
        return values

class LLMProposal(BaseModel):
    intervention: InterventionType
    proposed_retry_ts: Optional[datetime] = None
    proposed_incentive_pct: Optional[float] = None
    reasoning: str = Field(..., description="short natural-language justification")
    customer_copy: Optional[str] = None

class AgentResult(BaseModel):
    agent: str
    intervention: Optional[InterventionType] = None
    proposed_retry_ts: Optional[datetime] = None
    proposed_incentive_pct: Optional[float] = None
    reasoning: str = ""
    risk_score: Optional[float] = None
    liquidity_window_ok: Optional[bool] = None
    customer_copy: Optional[str] = None
    historical_context: List[str] = Field(default_factory=list)

class CustomerReply(BaseModel):
    invoice_id: str
    customer_id: str
    message: str
    received_at: datetime = Field(default_factory=datetime.now)

class GuardrailVerdict(BaseModel):
    final_intervention: InterventionType
    final_retry_ts: Optional[datetime] = None
    final_incentive_pct: Optional[float] = None
    was_overridden: bool
    override_reason: Optional[str] = None
    original_proposal: LLMProposal

class NegotiationResult(BaseModel):
    agent: str = "negotiator_agt"
    requested_retry_ts: Optional[datetime] = None
    requested_incentive_pct: Optional[float] = None
    proposal: LLMProposal
    verdict: GuardrailVerdict
    approved: bool
    response: str

class RecoveryEvent(BaseModel):
    id: Optional[int] = None
    invoice_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)

def normalize_gateway_payload(payload: Dict[str, Any]) -> RecoveryState:
    """
    Normalizes webhook payloads from various payment gateways (Stripe, Razorpay,
    Adyen, PayPal, or generic JSON) into a platform-agnostic RecoveryState.
    """
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a dictionary")

    # Direct RecoveryState JSON detection
    if "invoice_id" in payload and "customer_id" in payload and "plan_value_paise" in payload:
        return RecoveryState.model_validate(payload)

    # 1. Stripe Webhook Normalization
    # e.g., type: "invoice.payment_failed" or "payment_intent.payment_failed"
    if "type" in payload and ("invoice" in payload["type"] or "payment_intent" in payload["type"]):
        event_type = payload.get("type", "invoice.payment_failed")
        obj = payload.get("data", {}).get("object", {})
        invoice_id = obj.get("id") or obj.get("invoice") or payload.get("id", "inv_stripe_unknown")
        customer_id = obj.get("customer") or "cust_stripe_unknown"
        amount = obj.get("amount_due") or obj.get("amount") or 0
        currency = (obj.get("currency") or "usd").upper()
        attempt_count = obj.get("attempt_count", 1) - 1
        if attempt_count < 0:
            attempt_count = 0
        err_msg = (
            obj.get("last_payment_error", {}).get("message")
            or obj.get("last_payment_error", {}).get("code")
            or "STRIPE_PAYMENT_FAILED"
        )
        return RecoveryState(
            invoice_id=invoice_id,
            mandate_id=obj.get("subscription") or invoice_id,
            payment_id=obj.get("payment_intent") or obj.get("id"),
            transaction_id=payload.get("id"),
            customer_id=customer_id,
            merchant_id=payload.get("account") or "merch_stripe_default",
            mandate_type=MandateType.CARD,
            plan_value_paise=amount,
            currency=currency,
            retry_count=attempt_count,
            last_attempt_ts=datetime.now(),
            last_failure_reason=err_msg,
            gateway_event=event_type,
            gateway_name="stripe",
            mandate_status="active"
        )

    # 2. Razorpay Webhook Normalization (legacy ingest)
    # e.g., event: "payment.failed"
    if "event" in payload and "payload" in payload:
        entity = (
            payload.get("payload", {}).get("payment", {}).get("entity", {})
            or payload.get("payload", {}).get("subscription", {}).get("entity", {})
        )
        invoice_id = entity.get("invoice_id") or entity.get("order_id") or entity.get("id", "inv_rzp_unknown")
        payment_id = entity.get("id")
        customer_id = entity.get("customer_id") or entity.get("contact") or "cust_rzp_unknown"
        amount = entity.get("amount") or 0
        currency = (entity.get("currency") or "inr").upper()
        error_desc = entity.get("error_description") or entity.get("error_code") or "PAYMENT_FAILED"
        return RecoveryState(
            invoice_id=invoice_id,
            mandate_id=entity.get("subscription_id") or payment_id or invoice_id,
            payment_id=payment_id,
            transaction_id=payment_id,
            customer_id=customer_id,
            merchant_id="merch_rzp_default",
            mandate_type=MandateType.UPI_AUTOPAY if entity.get("method") == "upi" else MandateType.CARD,
            plan_value_paise=amount,
            currency=currency,
            retry_count=0,
            last_attempt_ts=datetime.now(),
            last_failure_reason=error_desc,
            gateway_event=payload.get("event"),
            gateway_name="razorpay",
            mandate_status="active"
        )

    # 3. Adyen Webhook Normalization
    if "notificationItems" in payload and isinstance(payload["notificationItems"], list) and len(payload["notificationItems"]) > 0:
        item = payload["notificationItems"][0].get("NotificationRequestItem", {})
        invoice_id = item.get("merchantReference") or item.get("pspReference", "inv_adyen_unknown")
        return RecoveryState(
            invoice_id=invoice_id,
            mandate_id=item.get("pspReference"),
            payment_id=item.get("pspReference"),
            transaction_id=item.get("pspReference"),
            customer_id=item.get("additionalData", {}).get("shopperReference", "cust_adyen_unknown"),
            merchant_id=item.get("merchantAccountCode", "merch_adyen_default"),
            mandate_type=MandateType.CARD,
            plan_value_paise=item.get("amount", {}).get("value", 0),
            currency=(item.get("amount", {}).get("currency") or "EUR").upper(),
            retry_count=0,
            last_attempt_ts=datetime.now(),
            last_failure_reason=item.get("reason", "ADYEN_AUTHORISATION_FAILED"),
            gateway_event=item.get("eventCode", "AUTHORISATION"),
            gateway_name="adyen",
            mandate_status="active"
        )

    # 4. PayPal Webhook Normalization
    if "event_type" in payload and "resource" in payload:
        res = payload.get("resource", {})
        invoice_id = res.get("invoice_number") or res.get("id", "inv_paypal_unknown")
        total_str = res.get("amount", {}).get("total", "0.00")
        try:
            total_cents = int(float(total_str) * 100)
        except Exception:
            total_cents = 0
        return RecoveryState(
            invoice_id=invoice_id,
            mandate_id=res.get("billing_agreement_id") or invoice_id,
            payment_id=res.get("id"),
            transaction_id=payload.get("id"),
            customer_id=res.get("payer", {}).get("payer_info", {}).get("payer_id", "cust_paypal_unknown"),
            merchant_id="merch_paypal_default",
            mandate_type=MandateType.CARD,
            plan_value_paise=total_cents,
            currency=(res.get("amount", {}).get("currency") or "USD").upper(),
            retry_count=0,
            last_attempt_ts=datetime.now(),
            last_failure_reason=payload.get("summary") or "PAYPAL_TRANSACTION_DENIED",
            gateway_event=payload.get("event_type"),
            gateway_name="paypal",
            mandate_status="active"
        )

    # 5. Generic / Universal Payment Gateway Payload Fallback
    invoice_id = payload.get("invoice_id") or payload.get("order_id") or payload.get("transaction_id") or payload.get("payment_id") or "inv_gen_001"
    customer_id = payload.get("customer_id") or payload.get("user_id") or payload.get("client_id") or "cust_gen_001"
    amount = payload.get("plan_value_paise") or payload.get("amount") or payload.get("amount_cents") or 0
    failure = payload.get("last_failure_reason") or payload.get("failure_reason") or payload.get("error_message") or "TRANSACTION_DECLINED"

    return RecoveryState(
        invoice_id=str(invoice_id),
        mandate_id=payload.get("mandate_id") or payload.get("subscription_id") or str(invoice_id),
        payment_id=payload.get("payment_id") or str(invoice_id),
        transaction_id=payload.get("transaction_id") or str(invoice_id),
        customer_id=str(customer_id),
        merchant_id=payload.get("merchant_id", "merch_default"),
        mandate_type=payload.get("mandate_type", MandateType.CARD),
        plan_value_paise=int(amount),
        currency=payload.get("currency", "INR"),
        retry_count=int(payload.get("retry_count", 0)),
        max_retry_budget=int(payload.get("max_retry_budget", 3)),
        last_attempt_ts=datetime.now(),
        last_failure_reason=failure,
        gateway_event=payload.get("gateway_event") or "payment.failed",
        gateway_name=payload.get("gateway_name", "universal"),
        mandate_status=payload.get("mandate_status", "active"),
        max_incentive_pct=float(payload.get("max_incentive_pct", 5.0)),
        metadata=payload.get("metadata", {})
    )

