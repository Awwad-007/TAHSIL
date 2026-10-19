import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from app.schemas import AgentResult, RecoveryState, RecoveryEvent

DEFAULT_DB_PATH = Path("tahsil.db")

class StateStore:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = str(db_path or DEFAULT_DB_PATH)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recovery_states (
                invoice_id TEXT PRIMARY KEY,
                customer_id TEXT,
                mandate_id TEXT,
                mandate_type TEXT,
                plan_value_paise INTEGER,
                retry_count INTEGER,
                mandate_status TEXT,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recovery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_locks (
                invoice_id TEXT NOT NULL,
                retry_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (invoice_id, retry_count)
            );
        """)
        conn.commit()

    def save_state(self, state: RecoveryState) -> RecoveryState:
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO recovery_states (
                invoice_id, customer_id, mandate_id, mandate_type,
                plan_value_paise, retry_count, mandate_status,
                state_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_id) DO UPDATE SET
                customer_id=excluded.customer_id,
                mandate_id=excluded.mandate_id,
                mandate_type=excluded.mandate_type,
                plan_value_paise=excluded.plan_value_paise,
                retry_count=excluded.retry_count,
                mandate_status=excluded.mandate_status,
                state_json=excluded.state_json,
                updated_at=excluded.updated_at;
        """, (
            state.invoice_id,
            state.customer_id,
            state.mandate_id,
            state.mandate_type.value,
            state.plan_value_paise,
            state.retry_count,
            state.mandate_status,
            state.model_dump_json(),
            datetime.now().isoformat()
        ))
        conn.commit()
        return state

    def get_state(self, invoice_id: str) -> Optional[RecoveryState]:
        conn = self._get_conn()
        row = conn.execute("SELECT state_json FROM recovery_states WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row:
            return RecoveryState.model_validate_json(row["state_json"])
        return None

    def list_states(self) -> List[RecoveryState]:
        conn = self._get_conn()
        rows = conn.execute("SELECT state_json FROM recovery_states ORDER BY updated_at DESC").fetchall()
        return [RecoveryState.model_validate_json(r["state_json"]) for r in rows]

    def log_event(self, invoice_id: str, event_type: str, payload: Dict[str, Any], timestamp: Optional[datetime] = None) -> RecoveryEvent:
        ts = (timestamp or datetime.now()).isoformat()
        payload_json = json.dumps(payload, default=str)

        conn = self._get_conn()
        cursor = conn.execute("""
            INSERT INTO recovery_events (invoice_id, timestamp, event_type, payload_json)
            VALUES (?, ?, ?, ?);
        """, (invoice_id, ts, event_type, payload_json))
        conn.commit()
        event_id = cursor.lastrowid
        return RecoveryEvent(
            id=event_id,
            invoice_id=invoice_id,
            timestamp=datetime.fromisoformat(ts),
            event_type=event_type,
            payload=payload
        )

    def log_agent_done(self, invoice_id: str, result: AgentResult) -> RecoveryEvent:
        names = {
            "triage_agt": "TRIAGE_AGENT_DONE",
            "schedule_agt": "SCHEDULE_AGENT_DONE",
            "copy_agt": "COPY_AGENT_DONE",
        }
        return self.log_event(
            invoice_id=invoice_id,
            event_type=names.get(result.agent, "AGENT_DONE"),
            payload=result.model_dump(mode="json"),
        )

    def get_events(self, invoice_id: Optional[str] = None, limit: int = 100) -> List[RecoveryEvent]:
        conn = self._get_conn()
        if invoice_id:
            rows = conn.execute("""
            SELECT id, invoice_id, timestamp, event_type, payload_json
            FROM recovery_events
            WHERE invoice_id = ?
            ORDER BY id DESC LIMIT ?
        """, (invoice_id, limit)).fetchall()
        else:
            rows = conn.execute("""
            SELECT id, invoice_id, timestamp, event_type, payload_json
            FROM recovery_events
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()

        events = []
        for r in rows:
            events.append(RecoveryEvent(
                id=r["id"],
                invoice_id=r["invoice_id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"])
            ))
        return events

    def is_locked(self, invoice_id: str, retry_count: int) -> bool:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT status FROM idempotency_locks
            WHERE invoice_id = ? AND retry_count = ?
        """, (invoice_id, retry_count)).fetchone()
        if row and row["status"] in ("in_flight", "completed"):
            return True
        return False

    def acquire_lock(self, invoice_id: str, retry_count: int, status: str = "in_flight") -> bool:
        conn = self._get_conn()
        try:
            conn.execute("""
            INSERT INTO idempotency_locks (invoice_id, retry_count, status, created_at)
            VALUES (?, ?, ?, ?);
        """, (invoice_id, retry_count, status, datetime.now().isoformat()))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def release_lock(self, invoice_id: str, retry_count: int, status: str = "completed"):
        conn = self._get_conn()
        conn.execute("""
            UPDATE idempotency_locks
            SET status = ?
            WHERE invoice_id = ? AND retry_count = ?;
        """, (status, invoice_id, retry_count))
        conn.commit()

    def get_metrics(self) -> Dict[str, Any]:
        conn = self._get_conn()
        total_states = conn.execute("SELECT COUNT(*) as c FROM recovery_states").fetchone()["c"]
        total_events = conn.execute("SELECT COUNT(*) as c FROM recovery_events").fetchone()["c"]
        
        verdict_rows = conn.execute("""
            SELECT payload_json FROM recovery_events WHERE event_type = 'GUARDRAIL_VERDICT'
        """).fetchall()
        
        total_verdicts = len(verdict_rows)
        overrides = 0
        interventions: Dict[str, int] = {}
        for r in verdict_rows:
            p = json.loads(r["payload_json"])
            verdict = p.get("verdict", {})
            if verdict.get("was_overridden"):
                overrides += 1
            fin = verdict.get("final_intervention", "UNKNOWN")
            interventions[fin] = interventions.get(fin, 0) + 1

        total_paise = conn.execute("SELECT SUM(plan_value_paise) as s FROM recovery_states").fetchone()["s"] or 0
        total_volume = total_paise / 100.0
        override_rate = round((overrides / total_verdicts * 100), 1) if total_verdicts > 0 else 0.0

        # Calculate recovered revenue & recovery efficiency
        # Active recovery includes strategic retries and personalized payment links
        active_recovery_count = interventions.get("RETRY_AT", 0) + interventions.get("SEND_PAYMENT_LINK", 0)
        recovery_rate_pct = round((active_recovery_count / total_verdicts * 100), 1) if total_verdicts > 0 else 0.0
        recovered_revenue = round(total_volume * (recovery_rate_pct / 100.0), 2) if total_volume > 0 else 0.0

        return {
            "total_states": total_states,
            "total_events": total_events,
            "total_verdicts": total_verdicts,
            "total_overrides": overrides,
            "override_rate_pct": override_rate,
            "total_volume_analyzed": total_volume,
            "total_value_at_risk_rupees": total_volume,
            "recovered_revenue": recovered_revenue,
            "recovery_rate_pct": recovery_rate_pct,
            "active_recovery_count": active_recovery_count,
            "interventions_breakdown": interventions
        }


    def clear_all(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM recovery_states;")
        conn.execute("DELETE FROM recovery_events;")
        conn.execute("DELETE FROM idempotency_locks;")
        conn.commit()

default_state_store = StateStore()
