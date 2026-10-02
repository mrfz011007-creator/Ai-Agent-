from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class StateStore:
    """Small SQLite state store for restart-safe agent state."""

    def __init__(self, path: str | Path = "agent_state.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
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
            if "evidence_id" not in artifact_columns:
                db.execute("ALTER TABLE artifacts ADD COLUMN evidence_id TEXT")
            db.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                f"SELECT task_id,status,attempts,payload,updated_at FROM tasks WHERE status IN ({placeholders}) ORDER BY updated_at ASC",
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
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO evidence(evidence_id,task_id,attempt_id,tool,action,success,result_status,error,payload) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    task_id,
                    attempt_id,
                    tool,
                    action,
                    int(success),
                    result_status,
                    error,
                    json.dumps(payload, sort_keys=True, default=str),
                ),
            )

    def load_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT evidence_id,task_id,attempt_id,tool,action,success,result_status,error,payload,created_at FROM evidence WHERE evidence_id=?",
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
                "SELECT evidence_id,task_id,attempt_id,tool,action,success,result_status,error,payload,created_at FROM evidence WHERE task_id=? ORDER BY rowid ASC",
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
                "SELECT artifact_id,task_id,attempt_id,path,kind,sha256,size,source_commit,payload,created_at "
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
