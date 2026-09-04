from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any

class MandateType(str, Enum):
    UPI_AUTOPAY = "upi_autopay"
    ENACH = "enach"
    CARD = "card"

class InterventionType(str, Enum):
    RETRY_AT = "RETRY_AT"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    PAUSE_MANDATE = "PAUSE_MANDATE"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    DO_NOTHING = "DO_NOTHING"

class RecoveryState(BaseModel):
    invoice_id: str
    mandate_id: str
    mandate_type: MandateType
    customer_id: str
    plan_value_paise: int
    retry_count: int = 0
    max_retry_budget: int = 3
    last_attempt_ts: Optional[datetime] = None
    last_failure_reason: Optional[str] = None
    predicted_liquidity_window_start: Optional[datetime] = None
    predicted_liquidity_window_end: Optional[datetime] = None
    bank_downtime_forecast: Optional[str] = None
    mandate_status: str = "active"
    max_incentive_pct: float = 5.0

class LLMProposal(BaseModel):
    intervention: InterventionType
    proposed_retry_ts: Optional[datetime] = None
    proposed_incentive_pct: Optional[float] = None
    reasoning: str = Field(..., description="short natural-language justification")

class GuardrailVerdict(BaseModel):
    final_intervention: InterventionType
    final_retry_ts: Optional[datetime] = None
    final_incentive_pct: Optional[float] = None
    was_overridden: bool
    override_reason: Optional[str] = None
    original_proposal: LLMProposal

class RecoveryEvent(BaseModel):
    id: Optional[int] = None
    invoice_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
