from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class StateStore:
    """Small SQLite state store for restart-safe agent state."""

    def __init__(self, path: str | Path = "agent_state.sqlite3"):
        self.path = Path(path)
        self._memory_connection: sqlite3.Connection | None = None
        if str(self.path) == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:")
            self._memory_connection.row_factory = sqlite3.Row
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    attempt_id TEXT,
                    tool TEXT NOT NULL,
                    action TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'execution',
                    authority TEXT NOT NULL DEFAULT 'execution_router',
                    success INTEGER NOT NULL,
                    result_status TEXT NOT NULL,
                    error TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(evidence)").fetchall()}
            if "attempt_id" not in columns:
                db.execute("ALTER TABLE evidence ADD COLUMN attempt_id TEXT")
            if "kind" not in columns:
                db.execute("ALTER TABLE evidence ADD COLUMN kind TEXT NOT NULL DEFAULT 'execution'")
            if "authority" not in columns:
                db.execute("ALTER TABLE evidence ADD COLUMN authority TEXT NOT NULL DEFAULT 'execution_router'")
            db.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    source_commit TEXT,
                    evidence_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            artifact_columns = {row[1] for row in db.execute("PRAGMA table_info(artifacts)").fetchall()}
            if "attempt_id" not in artifact_columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN attempt_id TEXT")
            if "evidence_id" not in artifact_columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN evidence_id TEXT")
            db.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    task_ids TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS agent_budget (
                    budget_id INTEGER PRIMARY KEY CHECK (budget_id = 1),
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def save_task(self, task_id: str, status: str, attempts: int, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO tasks(task_id,status,attempts,payload)
                   VALUES (?,?,?,?)
                   ON CONFLICT(task_id) DO UPDATE SET
                   status=excluded.status, attempts=excluded.attempts,
                   payload=excluded.payload, updated_at=CURRENT_TIMESTAMP""",
                (task_id, status, attempts, json.dumps(payload, sort_keys=True, default=str)),
            )

    def load_tasks_by_status(self, statuses: list[str]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT task_id,status,attempts,payload,updated_at FROM tasks WHERE status IN ({placeholders}) ORDER BY updated_at ASC",  # nosec B608: placeholders are generated internally; values are bound parameters.
                tuple(statuses),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            results.append(result)
        return results

    def load_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT task_id,status,attempts,payload,updated_at FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def save_evidence(
        self,
        evidence_id: str,
        task_id: str | None,
        attempt_id: str | None,
        tool: str,
        action: str,
        success: bool,
        result_status: str,
        error: str | None,
        payload: dict[str, Any],
        kind: str = "execution",
        authority: str = "execution_router",
    ) -> None:
        if kind != "execution" or authority != "execution_router":
            raise ValueError("Use save_reconciliation_evidence() for reconciliation evidence")
        with self._connect() as db:
            db.execute(
                "INSERT INTO evidence(evidence_id,task_id,attempt_id,tool,action,kind,authority,success,result_status,error,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    task_id,
                    attempt_id,
                    tool,
                    action,
                    kind,
                    authority,
                    int(success),
                    result_status,
                    error,
                    json.dumps(payload, sort_keys=True, default=str),
                ),
            )

    def save_reconciliation_evidence(
        self,
        evidence_id: str,
        task_id: str,
        attempt_id: str,
        tool: str,
        action: str,
        result_status: str,
        error: str | None,
        payload: dict[str, Any],
    ) -> None:
        if not task_id or not attempt_id:
            raise ValueError("Reconciliation evidence requires task and attempt IDs")
        with self._connect() as db:
            db.execute(
                "INSERT INTO evidence(evidence_id,task_id,attempt_id,tool,action,kind,authority,success,result_status,error,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    task_id,
                    attempt_id,
                    tool,
                    action,
                    "reconciliation",
                    "reconciliation_boundary",
                    1,
                    result_status,
                    error,
                    json.dumps(payload, sort_keys=True, default=str),
                ),
            )

    def load_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT evidence_id,task_id,attempt_id,tool,action,kind,authority,success,result_status,error,payload,created_at FROM evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["success"] = bool(result["success"])
        result["payload"] = json.loads(result["payload"])
        return result

    def load_evidence_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT evidence_id,task_id,attempt_id,tool,action,kind,success,result_status,error,payload,created_at FROM evidence WHERE task_id=? ORDER BY rowid ASC",
                (task_id,),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["success"] = bool(result["success"])
            result["payload"] = json.loads(result["payload"])
            results.append(result)
        return results

    def save_artifact(self, artifact: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO artifacts(
                    artifact_id,task_id,attempt_id,path,kind,sha256,size,
                    source_commit,evidence_id,payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact["artifact_id"],
                    artifact["task_id"],
                    artifact.get("attempt_id"),
                    artifact["path"],
                    artifact["kind"],
                    artifact["sha256"],
                    int(artifact["size"]),
                    artifact.get("source_commit"),
                    artifact.get("evidence_id"),
                    json.dumps(artifact, sort_keys=True, default=str),
                ),
            )

    def load_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT artifact_id,task_id,attempt_id,path,kind,sha256,size,source_commit,evidence_id,payload,created_at "
                "FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def load_artifacts_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT artifact_id,task_id,attempt_id,path,kind,sha256,size,source_commit,evidence_id,payload,created_at "
                "FROM artifacts WHERE task_id=? ORDER BY rowid ASC",
                (task_id,),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            results.append(result)
        return results

    def save_checkpoint(self, checkpoint_id: str, task_id: str | None, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO checkpoints(checkpoint_id,task_id,payload) VALUES (?,?,?)",
                (checkpoint_id, task_id, json.dumps(payload, sort_keys=True, default=str)),
            )

    def load_latest_checkpoint(self, task_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            query = "SELECT checkpoint_id,task_id,payload,created_at FROM checkpoints"
            params = ()
            if task_id is not None:
                query += " WHERE task_id=?"
                params = (task_id,)
            query += " ORDER BY rowid DESC LIMIT 1"
            row = db.execute(query, params).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def save_budget(self, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO agent_budget(budget_id,payload)
                   VALUES (1,?)
                   ON CONFLICT(budget_id) DO UPDATE SET
                   payload=excluded.payload, updated_at=CURRENT_TIMESTAMP""",
                (json.dumps(payload, sort_keys=True, default=str),),
            )

    def load_budget(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload,updated_at FROM agent_budget WHERE budget_id=1"
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        payload["updated_at"] = row["updated_at"]
        return payload

    def save_plan(
        self,
        plan_id: str,
        goal: str,
        status: str,
        task_ids: list[str] | tuple[str, ...],
        acceptance_criteria: list[str] | tuple[str, ...],
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO plans(plan_id,goal,status,task_ids,acceptance_criteria)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(plan_id) DO UPDATE SET
                   goal=excluded.goal,status=excluded.status,
                   task_ids=excluded.task_ids,
                   acceptance_criteria=excluded.acceptance_criteria,
                   updated_at=CURRENT_TIMESTAMP""",
                (
                    plan_id,
                    goal,
                    status,
                    json.dumps(list(task_ids)),
                    json.dumps(list(acceptance_criteria)),
                ),
            )

    def load_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT plan_id,goal,status,task_ids,acceptance_criteria,updated_at "
                "FROM plans WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["task_ids"] = json.loads(result["task_ids"])
        result["acceptance_criteria"] = json.loads(result["acceptance_criteria"])
        return result
