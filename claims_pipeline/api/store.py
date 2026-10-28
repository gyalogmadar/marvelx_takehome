"""SQLite store for submitted claims and their results.  One table, JSON for
the result tree; the uploaded files live on disk under data/claims/<id>/."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from claims_pipeline.domain import ClaimResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  policy TEXT NOT NULL,
  status TEXT NOT NULL,            -- processing | done | failed
  description TEXT NOT NULL,
  metadata TEXT NOT NULL,          -- json
  submitted_at TEXT,
  files TEXT NOT NULL,             -- json list of filenames
  result TEXT,                     -- json ClaimResult
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaimStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "claims.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(SCHEMA)

    def files_dir(self, claim_id: str) -> Path:
        d = self.data_dir / "claims" / claim_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def create(self, claim_id: str, policy: str, description: str, metadata: dict, submitted_at: datetime | None,
               filenames: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO claims (id, policy, status, description, metadata, submitted_at, files, created_at, updated_at)"
                " VALUES (?, ?, 'processing', ?, ?, ?, ?, ?, ?)",
                (claim_id, policy, description, json.dumps(metadata), submitted_at.isoformat() if submitted_at else None,
                 json.dumps(filenames), _now(), _now()),
            )

    def set_result(self, claim_id: str, result: ClaimResult) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE claims SET status='done', result=?, error=NULL, updated_at=? WHERE id=?",
                               (result.model_dump_json(), _now(), claim_id))

    def set_error(self, claim_id: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE claims SET status='failed', error=?, updated_at=? WHERE id=?", (error, _now(), claim_id))

    def get(self, claim_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        return self._to_record(row) if row else None

    def list(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM claims ORDER BY created_at DESC").fetchall()
        return [self._to_summary(r) for r in rows]

    @staticmethod
    def _to_record(row: sqlite3.Row) -> dict:
        return {
            "claim_id": row["id"], "policy": row["policy"], "status": row["status"],
            "description": row["description"], "metadata": json.loads(row["metadata"]),
            "submitted_at": row["submitted_at"], "files": json.loads(row["files"]),
            "result": json.loads(row["result"]) if row["result"] else None, "error": row["error"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @classmethod
    def _to_summary(cls, row: sqlite3.Row) -> dict:
        rec = cls._to_record(row)
        decision = (rec["result"] or {}).get("decision") or {}
        return {
            "claim_id": rec["claim_id"], "policy": rec["policy"], "status": rec["status"],
            "strategy": (rec["result"] or {}).get("strategy"),
            "submitted_at": rec["submitted_at"], "created_at": rec["created_at"], "files": rec["files"],
            "decision": decision.get("outcome"), "confidence": decision.get("confidence"),
            "payout": decision.get("payout"), "currency": decision.get("currency"), "summary": decision.get("summary"),
            "coverages_applied": decision.get("coverages_applied", []), "error": rec["error"],
            "description": rec["description"][:160],
        }
