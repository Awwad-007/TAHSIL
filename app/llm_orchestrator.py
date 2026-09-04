import os
from datetime import datetime, timedelta
import json
from typing import Optional
from dotenv import load_dotenv
from anthropic import Anthropic
from app.schemas import RecoveryState, LLMProposal, InterventionType

load_dotenv()

SYSTEM_PROMPT = """You are Tahsīl's AI Revenue Recovery Case-Worker Agent.
Your job is to analyze telemetry from a failed recurring subscription payment (UPI AutoPay, eNACH, or Card) and propose the single most effective recovery intervention from:
- RETRY_AT: Schedule a retry attempt at an optimal timestamp (e.g., within liquidity windows, or after payment failure cooldown).
- SEND_PAYMENT_LINK: Issue an instant one-click payment link (optionally with an incentive/discount percentage) if direct debit has friction.
- PAUSE_MANDATE: Temporarily pause the mandate to prevent repeated hard declines.
- ESCALATE_TO_HUMAN: Escalate to merchant operations or relationship managers.
- DO_NOTHING: No action required (e.g., mandate permanently dead or unrecoverable).

Provide a concise, 1-sentence reasoning justifying your recommendation.
NOTE: You do NOT need to enforce regulatory rules or compliance limits (such as NPCI retry caps, bank maintenance windows, or merchant discount caps) — an independent, deterministic downstream compliance engine validates and enforces all hard rules. Reason freely about the best recovery strategy."""

TOOL_SCHEMA = {
    "name": "propose_recovery_action",
    "description": "Emit a structured recovery intervention proposal for a failed recurring payment.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intervention": {
                "type": "string",
                "enum": ["RETRY_AT", "SEND_PAYMENT_LINK", "PAUSE_MANDATE", "ESCALATE_TO_HUMAN", "DO_NOTHING"],
                "description": "The recommended recovery action."
            },
            "proposed_retry_ts": {
                "type": "string",
                "description": "ISO 8601 timestamp string if intervention is RETRY_AT (e.g. 2026-09-05T10:00:00)."
            },
            "proposed_incentive_pct": {
                "type": "number",
                "description": "Discount or cashback percentage (0-100) if intervention is SEND_PAYMENT_LINK."
            },
            "reasoning": {
                "type": "string",
                "description": "A concise one-sentence natural language justification for the proposal."
            }
        },
        "required": ["intervention", "reasoning"]
    }
}

def _heuristic_propose(state: RecoveryState) -> LLMProposal:
    if state.mandate_status in ("revoked", "cancelled"):
        return LLMProposal(
            intervention=InterventionType.DO_NOTHING,
            proposed_retry_ts=None,
            proposed_incentive_pct=None,
            reasoning="Customer mandate has been revoked by user; cannot trigger automated debit or retry."
        )
    if state.retry_count >= state.max_retry_budget:
        retry_time = (state.last_attempt_ts or datetime.now()) + timedelta(days=1)
        return LLMProposal(
            intervention=InterventionType.RETRY_AT,
            proposed_retry_ts=retry_time,
            proposed_incentive_pct=None,
            reasoning="Customer has high historical lifetime value; attempting another automated debit retry after cooldown."
        )
    if state.bank_downtime_forecast and "02:00" in state.bank_downtime_forecast:
        retry_time = datetime(2026, 9, 5, 3, 30, 0)
        return LLMProposal(
            intervention=InterventionType.RETRY_AT,
            proposed_retry_ts=retry_time,
            proposed_incentive_pct=None,
            reasoning="Scheduling early morning debit to capture liquidity before daily customer expenditures."
        )
    if state.predicted_liquidity_window_start:
        return LLMProposal(
            intervention=InterventionType.RETRY_AT,
            proposed_retry_ts=state.predicted_liquidity_window_start + timedelta(hours=2),
            proposed_incentive_pct=None,
            reasoning=f"Aligning debit retry with predicted salary deposit liquidity window at {state.predicted_liquidity_window_start.strftime('%d %b %H:%M')}."
        )
    if state.plan_value_paise > 100000 and state.retry_count >= 2:
        return LLMProposal(
            intervention=InterventionType.SEND_PAYMENT_LINK,
            proposed_retry_ts=None,
            proposed_incentive_pct=10.0,
            reasoning="High-value plan with multiple debit failures; sending direct payment link with 10% instant discount."
        )
    base_time = state.last_attempt_ts or datetime.now()
    return LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=base_time + timedelta(hours=26),
        proposed_incentive_pct=None,
        reasoning=f"Transient failure due to {state.last_failure_reason or 'insufficient balance'}; scheduling retry after 24h standard cooldown."
    )

def propose_intervention(state: RecoveryState) -> LLMProposal:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or api_key.startswith("your_anthropic_api_key"):
        return _heuristic_propose(state)
    try:
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "propose_recovery_action"},
            messages=[{
                "role": "user",
                "content": f"Here is the failed recurring payment telemetry state JSON:\n{state.model_dump_json(indent=2)}\n\nPropose the best recovery intervention."
            }]
        )
        for content_block in response.content:
            if content_block.type == "tool_use":
                tool_input = content_block.input
                if tool_input.get("proposed_retry_ts") and isinstance(tool_input["proposed_retry_ts"], str):
                    try:
                        tool_input["proposed_retry_ts"] = datetime.fromisoformat(tool_input["proposed_retry_ts"].replace("Z", "+00:00"))
                    except Exception:
                        pass
                return LLMProposal.model_validate(tool_input)
        return _heuristic_propose(state)
    except Exception as e:
        print(f"[LLM Orchestrator Warning] Live API call failed: {e}. Using fallback reasoner.")
        return _heuristic_propose(state)
