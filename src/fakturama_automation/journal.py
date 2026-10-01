"""Local intent journal with one unresolved writer per accounting workspace.

An interrupted/failed run retains its lease. No automatic resume, retry, deletion,
or stale-lock takeover. Workspace must identify the same Fakturama database.
"""

import sqlite3
from pathlib import Path

from .domain import OrderInput, business_key, fingerprint
from .errors import ReviewRequired


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ReviewRequired("journal_symlink_rejected")
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        path.chmod(0o600)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          workspace TEXT NOT NULL, job_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
          state TEXT NOT NULL, order_number TEXT, invoice_number TEXT,
          PRIMARY KEY(workspace, job_key));
        CREATE TABLE IF NOT EXISTS leases (
          workspace TEXT PRIMARY KEY, job_key TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, workspace TEXT NOT NULL, job_key TEXT NOT NULL,
          stage TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def begin(self, workspace: str, source: OrderInput) -> str:
        key = business_key(source)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT fingerprint,state FROM jobs WHERE workspace=? AND job_key=?",
                                  (workspace, key)).fetchone()
            if row:
                raise ReviewRequired("duplicate_job" if row[0] == fingerprint(source) else "changed_input_same_reference")
            if self.db.execute("SELECT 1 FROM leases WHERE workspace=?", (workspace,)).fetchone():
                raise ReviewRequired("workspace_has_unresolved_run")
            self.db.execute("INSERT INTO jobs(workspace,job_key,fingerprint,state) VALUES(?,?,?,?)",
                            (workspace, key, fingerprint(source), "RUNNING"))
            self.db.execute("INSERT INTO leases VALUES(?,?)", (workspace, key))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return key

    def event(self, workspace: str, key: str, stage: str, status: str):
        self.db.execute("INSERT INTO events(workspace,job_key,stage,status) VALUES(?,?,?,?)",
                        (workspace, key, stage, status))

    def mark_review(self, workspace: str, key: str):
        self.db.execute("UPDATE jobs SET state='REVIEW' WHERE workspace=? AND job_key=?", (workspace, key))

    def document_numbers(self, workspace: str, key: str, *, order: str | None = None, invoice: str | None = None):
        self.db.execute("UPDATE jobs SET order_number=COALESCE(?,order_number), invoice_number=COALESCE(?,invoice_number) WHERE workspace=? AND job_key=?",
                        (order, invoice, workspace, key))

    def finish(self, workspace: str, key: str):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE jobs SET state='VERIFIED' WHERE workspace=? AND job_key=?", (workspace, key))
            self.db.execute("DELETE FROM leases WHERE workspace=? AND job_key=?", (workspace, key))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def summary(self) -> list[dict]:
        cur = self.db.execute("SELECT workspace,job_key,state,order_number,invoice_number FROM jobs ORDER BY rowid")
        return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
