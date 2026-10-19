"""
TAHSĪL (تحصيل) — Interactive Narrated Demo Script
===================================================
Autonomous AI Revenue Recovery Engine

This script demonstrates Tahsil's neuro-symbolic recovery pipeline:
1. Ingesting failed recurring payment events across global payment rails (Cards, UPI AutoPay, eNACH, ACH, SEPA).
2. LLM reasoning and generating candidate recovery interventions.
3. Pure-Python deterministic Guardrail Engine enforcing compliance rules (retry caps, 24h cooldowns, maintenance windows).
4. Platform-agnostic gateway execution and live audit logging.
5. Highlight: The Staged Failure moment (Rule 2: Retry Budget Exhausted).
"""

import sys
import pathlib
# Ensure project root is in sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Ensure Windows terminal UTF-8 output
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncio
import time
from datetime import datetime
from app.schemas import RecoveryState, InterventionType
from app.simulator import get_demo_scenarios
from app.llm_orchestrator import propose_intervention_parallel
from app.guardrail_engine import evaluate_proposal
from app.executor import execute
from app.state_store import default_state_store
from app.vector_index import seed_history

# ANSI terminal colors for presentation
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_banner():
    banner = f"""
{CYAN}{BOLD}+============================================================================+
|                                                                            |
|       TAHSĪL (تحصيل) — Autonomous AI Revenue Recovery Engine               |
|       Enterprise Neuro-Symbolic Agent Middleware for Global Payments        |
|                                                                            |
|       Architecture: Neuro-Symbolic Hybrid (LLM + Deterministic Guardrails) |
|                                                                            |
+============================================================================+{RESET}
"""
    print(banner)


def format_inr(paise: int) -> str:
    return f"Rs. {paise / 100:,.2f}"


def run_scenario(name: str, state: RecoveryState, index: int, total: int):
    print(f"\n{BOLD}{'='*80}{RESET}")
    print(f"{BLUE}{BOLD}>> SCENARIO {index}/{total}: {name.upper()}{RESET}")
    print(f"{BOLD}{'='*80}{RESET}")

    # 1. State Ingestion
    print(f"\n{CYAN}{BOLD}[1. TELEMETRY INGESTION]{RESET}")
    print(f"  * Invoice ID       : {BOLD}{state.invoice_id}{RESET}")
    print(f"  * Customer ID      : {state.customer_id}")
    print(f"  * Gateway / Rail   : {state.gateway_name.upper()} ({state.mandate_type.value.upper()})")
    print(f"  * Transaction Ref  : {state.transaction_id or state.payment_id}")
    print(f"  * Mandate Status   : {state.mandate_status.upper()}")
    print(f"  * Amount at Risk   : {BOLD}{format_inr(state.plan_value_paise)}{RESET}")
    print(f"  * Retry Attempt    : {state.retry_count} of {state.max_retry_budget} max budget")
    print(f"  * Last Failure     : {YELLOW}{state.last_failure_reason or 'None'}{RESET}")
    if state.predicted_liquidity_window_start:
        print(f"  * Liquidity Window : {GREEN}{state.predicted_liquidity_window_start.strftime('%d %b %H:%M')} - {state.predicted_liquidity_window_end.strftime('%H:%M')}{RESET}")
    if state.bank_downtime_forecast:
        print(f"  * Bank Maintenance : {RED}{state.bank_downtime_forecast}{RESET}")

    default_state_store.save_state(state)
    default_state_store.log_event(
        invoice_id=state.invoice_id,
        event_type="WEBHOOK_RECEIVED",
        payload=state.model_dump(mode="json")
    )
    time.sleep(0.3)

    # 2. LLM Reasoning
    print(f"\n{MAGENTA}{BOLD}[2. NEURAL REASONING — MULTI-AGENT SWARM (GPT-6 Astra / Heuristic Fallback)]{RESET}")
    start_llm = time.time()
    print(f"  {CYAN}↳ triage_agt  / schedule_agt  / copy_agt launched concurrently...{RESET}")

    def agent_done(_name, res):
        default_state_store.log_agent_done(state.invoice_id, res)
        print(f"  {GREEN}✓ {res.agent} DONE{RESET}  {CYAN}{res.reasoning}{RESET}")

    proposal, agents = asyncio.run(propose_intervention_parallel(state, on_done=agent_done))
    elapsed = time.time() - start_llm
    default_state_store.log_event(
        invoice_id=state.invoice_id,
        event_type="LLM_PROPOSED",
        payload={
            "proposal": proposal.model_dump(mode="json"),
            "agents": {name: res.model_dump(mode="json") for name, res in agents.items()},
        }
    )
    print(f"  * Proposed Action  : {BOLD}{proposal.intervention.value}{RESET}")
    if proposal.proposed_retry_ts:
        print(f"  * Proposed Retry Ts: {proposal.proposed_retry_ts.isoformat()}")
    if proposal.proposed_incentive_pct:
        print(f"  * Proposed Discount: {proposal.proposed_incentive_pct}%")
    print(f"  * LLM Reasoning    : {CYAN}\"{proposal.reasoning}\"{RESET}")
    cpy = agents.get("copy_agt")
    if cpy and cpy.historical_context:
        print(f"  * Retrieved History: {CYAN}{cpy.historical_context[0]}{RESET}")
        print(f"  * Contextual Copy  : {CYAN}\"{cpy.customer_copy}\"{RESET}")
    print(f"  * Inference Latency: {elapsed:.2f}s")
    time.sleep(0.3)

    # Special Spotlight for Staged Failure Moment
    if name == "scenario_exhausted_budget":
        print(f"\n{RED}{BOLD}!! [CRITICAL STAGED FAILURE MOMENT] !!{RESET}")
        print(f"{RED}  Notice: The LLM attempted to be 'helpful' by proposing a 4th retry for a high-value customer,{RESET}")
        print(f"{RED}  violating the maximum compliance retry budget cap (3/3). Directly executing this would trigger{RESET}")
        print(f"{RED}  bank penalty fees and customer harassment complaints.{RESET}")
        time.sleep(0.5)

    # 3. Guardrail Validation
    print(f"\n{YELLOW}{BOLD}[3. SYMBOLIC VERIFICATION — DETERMINISTIC GUARDRAIL ENGINE]{RESET}")
    verdict = evaluate_proposal(state, proposal, store=default_state_store)
    default_state_store.log_event(
        invoice_id=state.invoice_id,
        event_type="GUARDRAIL_VERDICT",
        payload={"verdict": verdict.model_dump(mode="json")}
    )
    
    if verdict.was_overridden:
        print(f"  * Guardrail Verdict: {RED}{BOLD}OVERRIDDEN / CLAMPED{RESET}")
        print(f"  * Override Reason  : {RED}{verdict.override_reason}{RESET}")
        print(f"  * Original Action  : {proposal.intervention.value} -> Final Action: {BOLD}{verdict.final_intervention.value}{RESET}")
    else:
        print(f"  * Guardrail Verdict: {GREEN}{BOLD}PASSED — ALL POLICIES COMPLIANT{RESET}")
        print(f"  * Status           : Pass-through (Zero policy violations)")
        print(f"  * Final Action     : {BOLD}{verdict.final_intervention.value}{RESET}")

    if verdict.final_retry_ts:
        print(f"  * Final Retry Time : {verdict.final_retry_ts.isoformat()}")
    if verdict.final_incentive_pct is not None:
        print(f"  * Final Incentive  : {verdict.final_incentive_pct}%")
    time.sleep(0.3)

    # 4. Mocked Execution
    print(f"\n{GREEN}{BOLD}[4. SAFE DISPATCH — UNIVERSAL GATEWAY EXECUTOR & AUDIT]{RESET}")
    exec_result = execute(verdict, state, store=default_state_store)
    
    call = exec_result.get("gateway_call")
    if call:
        print(f"  * Gateway Dispatch : {BOLD}{call['method']} {call['endpoint']} [{call.get('gateway', 'universal').upper()}]{RESET}")
        if "payment_link_url" in exec_result:
            print(f"  * Payment Link Url : {CYAN}{exec_result['payment_link_url']}{RESET}")
        if "customer_recovery_message" in exec_result:
            print(f"  * Empathetic Msg   : {CYAN}\"{exec_result['customer_recovery_message']}\"{RESET}")
    elif "ops_alert" in exec_result:
        alert = exec_result["ops_alert"]
        print(f"  * Ops Alert Queue  : {RED}{BOLD}{alert['target']} [{alert['body']['alert']}]{RESET}")
        print(f"  * Escalation Note  : {alert['body']['reason']}")
    else:
        print(f"  * Action Dispatched: {exec_result.get('action_taken', 'NO_OP')}")
    
    print(f"  * Audit Log Status : {GREEN}Persisted to SQLite with idempotency lock{RESET}")
    time.sleep(0.3)


def main():
    print_banner()
    
    scenarios = get_demo_scenarios()
    scenarios["scenario_contextual_outreach"] = RecoveryState(
        invoice_id="inv_ctx_007",
        mandate_id="sub_ctx_card_707",
        payment_id="pay_ctx_707",
        transaction_id="txn_ctx_707",
        customer_id="cust_context_007",
        merchant_id="merch_saas_02",
        plan_value_paise=249900,
        currency="INR",
        retry_count=1,
        max_retry_budget=3,
        last_attempt_ts=datetime(2026, 9, 3, 10, 0),
        last_failure_reason="U30_INSUFFICIENT_FUNDS",
        gateway_event="payment.failed",
        gateway_name="universal",
        mandate_status="active",
        max_incentive_pct=5.0,
    )
    seed_history([
        {
            "customer_id": "cust_context_007",
            "text": "Customer previously requested shifting the monthly billing date to the 10th after salary credit.",
        },
        {
            "customer_id": "cust_context_007",
            "text": "Customer prefers a short, respectful payment reminder without repeated notifications.",
        },
    ])
    total = len(scenarios)
    
    print(f"{BOLD}Initializing Tahsil State Store and loading {total} production-grade recurring payment failure scenarios...{RESET}\n")
    default_state_store.clear_all()

    for idx, (name, state) in enumerate(scenarios.items(), start=1):
        run_scenario(name, state, idx, total)

    # Summary Telemetry
    metrics = default_state_store.get_metrics()
    print(f"\n{CYAN}{BOLD}{'='*80}{RESET}")
    print(f"{CYAN}{BOLD}TAHSIL DEMO TELEMETRY & REVENUE RECOVERY SUMMARY{RESET}")
    print(f"{CYAN}{BOLD}{'='*80}{RESET}")
    print(f"  * Total Transactions Ingested         : {BOLD}{metrics['total_states']}{RESET}")
    print(f"  * Total Guardrail Decisions Evaluated : {BOLD}{metrics['total_verdicts']}{RESET}")
    print(f"  * LLM Proposals Overridden by Rules   : {RED}{BOLD}{metrics['total_overrides']}{RESET}")
    print(f"  * Deterministic Override Rate         : {YELLOW}{BOLD}{metrics['override_rate_pct']}%{RESET}")
    print(f"  * Total Volume Analyzed               : {BOLD}Rs. {metrics['total_volume_analyzed']:,.2f}{RESET}")
    print(f"  * Recovered Revenue Potential         : {GREEN}{BOLD}Rs. {metrics['recovered_revenue']:,.2f}{RESET}")
    print(f"  * Active Recovery Rate                : {GREEN}{BOLD}{metrics['recovery_rate_pct']}%{RESET}")
    print(f"  * Interventions Breakdown             : {metrics['interventions_breakdown']}")
    print(f"\n{GREEN}{BOLD}[OK] Demo completed successfully.{RESET}")
    print(f"Open {BOLD}http://127.0.0.1:8000/dashboard{RESET} to view the live dashboard visualization.\n")


if __name__ == "__main__":
    main()
