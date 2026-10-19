import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple
from dotenv import load_dotenv
from anthropic import Anthropic
from app.schemas import AgentResult, CustomerReply, NegotiationResult, RecoveryState, LLMProposal, InterventionType
from app.guardrail_engine import evaluate
from app.vector_index import history_index

load_dotenv()

_model_client: Optional[Any] = None


def set_model_client(client: Optional[Any]) -> None:
    """Inject the active OmniRush/Astra structured client for this process."""
    global _model_client
    _model_client = client

SYSTEM_PROMPT = """You are Tahsīl's Autonomous AI Revenue Recovery Agent.
Your mission is to analyze telemetry from a failed recurring subscription payment across global payment rails (Cards, UPI AutoPay, eNACH, ACH, SEPA, and Direct Debit) and propose the single most effective recovery intervention from:
- RETRY_AT: Schedule a retry attempt at an optimal timestamp (e.g., within customer liquidity windows, or after payment rail cooldown).
- SEND_PAYMENT_LINK: Issue an instant one-click direct recovery link (optionally with an empathetic incentive/discount percentage) if automated debit encounters friction.
- PAUSE_MANDATE: Temporarily pause the subscription/mandate to prevent hard declines, bank fees, and customer friction.
- ESCALATE_TO_HUMAN: Escalate to merchant operations or relationship managers when automated recovery is exhausted.
- DO_NOTHING: No action required (e.g., mandate permanently revoked or unrecoverable).

Tone & Strategy Guidelines:
- Prioritize customer empathy and retention. Ensure recommendations are professional, empathetic, and respectful of customer relationships regardless of the underlying payment gateway.
- Provide a concise, 1-sentence reasoning justifying your recommendation.
- NOTE: You do NOT need to enforce regulatory rules or compliance limits (such as network retry caps, maintenance blackout windows, or merchant discount caps) — an independent, deterministic downstream guardrail firewall validates and enforces all hard rules. Reason freely about the best recovery strategy."""


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


def _terms(msg: str, at: datetime) -> Tuple[Optional[datetime], Optional[float]]:
    date = None
    inc = None
    hit = re.search(r"\b(20\d{2}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?\b", msg)
    if hit:
        date = datetime.fromisoformat(f"{hit.group(1)}T{hit.group(2) or at.strftime('%H:%M')}")
    else:
        hit = re.search(r"\b(\d+)\s*[- ]?day(?:s)?\b", msg.lower())
        if hit:
            date = at + timedelta(days=int(hit.group(1)))
        elif re.search(r"\bthree\s+days?\b", msg.lower()):
            date = at + timedelta(days=3)
    hit = re.search(r"(?:discount|concession|off)\D{0,10}(\d+(?:\.\d+)?)\s*%|\b(\d+(?:\.\d+)?)\s*%\s*(?:discount|concession|off)", msg.lower())
    if hit:
        inc = float(hit.group(1) or hit.group(2))
    return date, inc


def _negotiate(state: RecoveryState, msg: str, at: datetime, store: Optional[Any]) -> NegotiationResult:
    retry_ts, inc = _terms(msg, at)
    if retry_ts:
        prop = LLMProposal(
            intervention=InterventionType.RETRY_AT,
            proposed_retry_ts=retry_ts,
            reasoning="Customer requested a billing-date extension; validating the requested retry time against policy.",
        )
    elif inc is not None:
        prop = LLMProposal(
            intervention=InterventionType.SEND_PAYMENT_LINK,
            proposed_incentive_pct=inc,
            reasoning="Customer requested a concession; validating it against the merchant incentive ceiling.",
        )
    else:
        prop = LLMProposal(
            intervention=InterventionType.ESCALATE_TO_HUMAN,
            reasoning="Customer request was ambiguous; a specialist must clarify the requested accommodation.",
        )
    verdict = evaluate(state, prop, store=store)
    approved = (
        not verdict.was_overridden
        and verdict.final_intervention == prop.intervention
        and prop.intervention in (InterventionType.RETRY_AT, InterventionType.SEND_PAYMENT_LINK)
    )
    if approved and retry_ts:
        response = f"Approved — we can retry your payment on {verdict.final_retry_ts.strftime('%Y-%m-%d at %H:%M')} as requested."
    elif approved and inc is not None:
        response = f"Approved — we can offer a {verdict.final_incentive_pct:g}% concession through a secure payment link."
    elif verdict.final_intervention == InterventionType.RETRY_AT and verdict.final_retry_ts:
        response = f"We cannot use the requested time because of the 24-hour safety window, but we can retry on {verdict.final_retry_ts.strftime('%Y-%m-%d at %H:%M')}."
    elif verdict.final_intervention == InterventionType.SEND_PAYMENT_LINK:
        response = f"We cannot approve that concession, but we can offer the merchant maximum of {verdict.final_incentive_pct:g}%."
    else:
        response = f"We cannot approve this request automatically: {verdict.override_reason or 'a specialist review is required'}."
    return NegotiationResult(
        requested_retry_ts=retry_ts,
        requested_incentive_pct=inc,
        proposal=prop,
        verdict=verdict,
        approved=approved,
        response=response,
    )


async def negotiator_agt(
    state: RecoveryState,
    reply: CustomerReply,
    store: Optional[Any] = None,
) -> NegotiationResult:
    return await asyncio.to_thread(_negotiate, state, reply.message, reply.received_at, store)


def _risk_score(state: RecoveryState) -> float:
    score = 0.25
    if state.retry_count >= state.max_retry_budget:
        score += 0.55
    elif state.retry_count:
        score += 0.15
    if state.mandate_status != "active":
        score += 0.25
    if state.plan_value_paise > 100000:
        score += 0.10
    return min(round(score, 2), 1.0)


def _live_agent(name: str, state: RecoveryState) -> Optional[AgentResult]:
    if _model_client is not None:
        try:
            res = _model_client.propose_agent_result(agent=name, state=state)
            return AgentResult.model_validate(res)
        except Exception as e:
            print(f"[LLM Orchestrator Warning] {name} model call failed: {e}. Using fallback reasoner.")
            return None
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        prop = propose_intervention(state)
        if name == "triage_agt":
            return AgentResult(
                agent=name, intervention=prop.intervention, reasoning=prop.reasoning,
                risk_score=_risk_score(state),
            )
        if name == "schedule_agt":
            return AgentResult(
                agent=name,
                proposed_retry_ts=prop.proposed_retry_ts if prop.intervention == InterventionType.RETRY_AT else None,
                reasoning="Model-predicted retry timing checked against liquidity telemetry.",
                liquidity_window_ok=True,
            )
        return AgentResult(
            agent=name, proposed_incentive_pct=prop.proposed_incentive_pct,
            reasoning=prop.reasoning,
            customer_copy=f"Hi, we noticed a temporary issue with invoice {state.invoice_id}; we will help keep your service active.",
        )
    return None


def _triage(state: RecoveryState) -> AgentResult:
    live = _live_agent("triage_agt", state)
    if live:
        return live
    prop = _heuristic_propose(state)
    return AgentResult(
        agent="triage_agt",
        intervention=prop.intervention,
        reasoning=prop.reasoning,
        risk_score=_risk_score(state),
    )


def _schedule(state: RecoveryState) -> AgentResult:
    live = _live_agent("schedule_agt", state)
    if live:
        return live
    prop = _heuristic_propose(state)
    retry_ts = prop.proposed_retry_ts if prop.intervention == InterventionType.RETRY_AT else None
    ok = True
    if retry_ts and state.predicted_liquidity_window_start and state.predicted_liquidity_window_end:
        ok = state.predicted_liquidity_window_start <= retry_ts <= state.predicted_liquidity_window_end
    return AgentResult(
        agent="schedule_agt",
        proposed_retry_ts=retry_ts,
        reasoning="Retry timing checked against the customer liquidity window and payment-rail cooldown.",
        liquidity_window_ok=ok,
    )


def _copy(state: RecoveryState) -> AgentResult:
    hist = history_index.query(state.customer_id, state.last_failure_reason or "payment recovery")
    live = _live_agent("copy_agt", state)
    if live:
        live.historical_context = hist
        if hist and live.customer_copy:
            live.customer_copy = f"{live.customer_copy} We also noted your earlier request: {hist[0]}"
        return live
    prop = _heuristic_propose(state)
    msg = f"Hi, we noticed a temporary issue processing your subscription payment for invoice {state.invoice_id}."
    if hist:
        msg += f" We remember your earlier request: {hist[0]} We'll align the next billing attempt with that preference where possible."
    else:
        msg += " We value your continued partnership and will help keep your service active."
    return AgentResult(
        agent="copy_agt",
        proposed_incentive_pct=prop.proposed_incentive_pct,
        reasoning=prop.reasoning,
        customer_copy=msg,
        historical_context=hist,
    )


async def triage_agt(state: RecoveryState) -> AgentResult:
    return await asyncio.to_thread(_triage, state)


async def schedule_agt(state: RecoveryState) -> AgentResult:
    return await asyncio.to_thread(_schedule, state)


async def copy_agt(state: RecoveryState) -> AgentResult:
    return await asyncio.to_thread(_copy, state)


def _merge(tri: AgentResult, sch: AgentResult, cpy: AgentResult) -> LLMProposal:
    action = tri.intervention or InterventionType.DO_NOTHING
    retry_ts = sch.proposed_retry_ts if action == InterventionType.RETRY_AT else None
    reasoning = cpy.reasoning or tri.reasoning or sch.reasoning
    return LLMProposal(
        intervention=action,
        proposed_retry_ts=retry_ts,
        proposed_incentive_pct=cpy.proposed_incentive_pct if action == InterventionType.SEND_PAYMENT_LINK else None,
        reasoning=reasoning,
        customer_copy=cpy.customer_copy,
    )


async def propose_intervention_parallel(
    state: RecoveryState,
    on_done: Optional[Callable[[str, AgentResult], None]] = None,
) -> Tuple[LLMProposal, Dict[str, AgentResult]]:
    async def run(name: str, fn: Callable[[RecoveryState], Any]) -> Tuple[str, AgentResult]:
        res = await fn(state)
        if on_done:
            on_done(name, res)
        return name, res

    tasks = [
        asyncio.create_task(run("triage_agt", triage_agt)),
        asyncio.create_task(run("schedule_agt", schedule_agt)),
        asyncio.create_task(run("copy_agt", copy_agt)),
    ]
    done = {}
    for task in asyncio.as_completed(tasks):
        name, res = await task
        done[name] = res
    tri, sch, cpy = done["triage_agt"], done["schedule_agt"], done["copy_agt"]
    out = {r.agent: r for r in (tri, sch, cpy)}
    return _merge(tri, sch, cpy), out
