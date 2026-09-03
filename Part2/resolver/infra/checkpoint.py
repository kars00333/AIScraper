"""SQLite-based checkpointing for resumable runs."""

import sqlite3
import csv
import logging
import threading
from datetime import datetime, timezone

from resolver.config import CHECKPOINT_DB_PATH

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS results (
    linkedin_url TEXT PRIMARY KEY,
    company TEXT,
    career_page_url TEXT,
    ats TEXT DEFAULT '',
    status TEXT NOT NULL,
    resolved_via TEXT DEFAULT '',
    timestamp TEXT NOT NULL
)
"""


class Checkpoint:
    """Resumable state backed by SQLite — one row per linkedin_url."""
    def __init__(self, db_path: str = CHECKPOINT_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # sqlite3 does not serialize concurrent use of one connection, so
        # every method touching self.conn must hold this lock — reads
        # included, since an overlapping SELECT loses the row.
        self._lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(CREATE_TABLE)
        self.conn.commit()
        logger.info("Checkpoint DB opened at %s", db_path)
    def already_done(self, linkedin_url: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "SELECT 1 FROM results WHERE linkedin_url = ?", (linkedin_url,)
            )
            return cur.fetchone() is not None
    def save(
        self,
        linkedin_url: str,
        company: str | None,
        career_page_url: str | None,
        ats: str,
        status: str,
        resolved_via: str = "",
    ) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO results
                   (linkedin_url, company, career_page_url, ats, status, resolved_via, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    linkedin_url,
                    company or "",
                    career_page_url or "",
                    ats,
                    status,
                    resolved_via,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()
    def get(self, linkedin_url: str) -> dict | None:
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM results WHERE linkedin_url = ?", (linkedin_url,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))
    def export_csv(self, path: str) -> int:
        """Dump all results to CSV. Returns row count."""
        with self._lock:
            cur = self.conn.execute(
                "SELECT linkedin_url, company, career_page_url, ats, status FROM results"
            )
            rows = cur.fetchall()
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["linkedin_url", "company", "career_page_url", "ats", "status"])
            writer.writerows(rows)
        logger.info("Exported %d rows to %s", len(rows), path)
        return len(rows)
    def export_predictions_csv(self, path: str) -> int:
        """Dump predictions in scorer format: linkedin_url,predicted_url,ats."""
        with self._lock:
            cur = self.conn.execute(
                "SELECT linkedin_url, career_page_url, ats FROM results"
            )
            rows = cur.fetchall()
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["linkedin_url", "predicted_url", "ats"])
            writer.writerows(rows)
        logger.info("Exported %d predictions to %s", len(rows), path)
        return len(rows)
    def resolved_rows(self) -> list[dict]:
        """Every row that produced a career page."""
        with self._lock:
            cur = self.conn.execute(
                "SELECT linkedin_url, company, career_page_url, ats FROM results "
                "WHERE career_page_url <> ''"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    def update_url(self, linkedin_url: str, url: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE results SET career_page_url = ? WHERE linkedin_url = ?",
                (url, linkedin_url),
            )
            self.conn.commit()
    def update_ats(self, linkedin_url: str, ats: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE results SET ats = ? WHERE linkedin_url = ?", (ats, linkedin_url)
            )
            self.conn.commit()
    def status_counts(self) -> dict:
        with self._lock:
            cur = self.conn.execute(
                "SELECT status, COUNT(*) FROM results GROUP BY status"
            )
            return dict(cur.fetchall())
    def count(self) -> int:
        with self._lock:
            cur = self.conn.execute("SELECT COUNT(*) FROM results")
            return cur.fetchone()[0]
    def clear(self) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM results")
            self.conn.commit()
    def close(self):
        with self._lock:
            self.conn.close()
