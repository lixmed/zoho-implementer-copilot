"""Append-only audit log (SQLite) for every call that changes something in Zoho.

Records who, what, when, which org/environment, before and after. Never tokens.
"""

from __future__ import annotations

import getpass
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copilot.orgs.registry import home_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    org         TEXT NOT NULL,
    environment TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT NOT NULL,
    project     TEXT,
    plan_version INTEGER,
    before_json TEXT,
    after_json  TEXT,
    result      TEXT NOT NULL,
    detail      TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or home_dir() / "audit.sqlite"
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def record(
        self,
        *,
        org: str,
        environment: str,
        action: str,
        target: str,
        result: str,
        before: Any = None,
        after: Any = None,
        detail: str | None = None,
        project: str | None = None,
        plan_version: int | None = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO audit (ts, actor, org, environment, action, target, project, "
                "plan_version, before_json, after_json, result, detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(UTC).isoformat(),
                    getpass.getuser(),
                    org,
                    environment,
                    action,
                    target,
                    project,
                    plan_version,
                    json.dumps(before) if before is not None else None,
                    json.dumps(after) if after is not None else None,
                    result,
                    detail,
                ),
            )
            return int(cur.lastrowid)

    def entries(self, org: str | None = None, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            sql = "SELECT * FROM audit"
            args: tuple = ()
            if org:
                sql += " WHERE org = ?"
                args = (org,)
            sql += " ORDER BY id DESC LIMIT ?"
            return [dict(r) for r in c.execute(sql, (*args, limit))]
