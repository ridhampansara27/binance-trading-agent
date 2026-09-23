"""SQLite journal for demo trading observability."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TradeJournal:
    def __init__(self, db_path: str = "data/trade_journal.sqlite3"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    order_ref TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incidents(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pnl_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    realized_pnl REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=UTC).isoformat()

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def log_signal(self, symbol: str, side: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signals(ts,symbol,side,payload) VALUES (?,?,?,?)",
                (self._now(), symbol, side, self._json(payload)),
            )

    def log_order(self, symbol: str, phase: str, order_ref: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO orders(ts,symbol,phase,order_ref,payload) VALUES (?,?,?,?,?)",
                (self._now(), symbol, phase, order_ref, self._json(payload)),
            )

    def log_position_event(self, symbol: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO positions(ts,symbol,event_type,payload) VALUES (?,?,?,?)",
                (self._now(), symbol, event_type, self._json(payload)),
            )

    def log_incident(
        self,
        severity: str,
        code: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO incidents(ts,severity,code,message,payload) VALUES (?,?,?,?,?)",
                (self._now(), severity, code, message, self._json(payload or {})),
            )

    def log_realized_pnl(self, symbol: str, realized_pnl: float, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pnl_events(ts,symbol,realized_pnl,payload) VALUES (?,?,?,?)",
                (self._now(), symbol, realized_pnl, self._json(payload)),
            )

    def export_table_csv(self, table_name: str, output_path: str) -> None:
        allowed = {"signals", "orders", "positions", "incidents", "pnl_events"}
        if table_name not in allowed:
            raise ValueError(f"Unsupported table: {table_name}")

        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM {table_name} ORDER BY id").fetchall()

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if not rows:
                return
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow(list(row))

    def daily_summary(self, day_iso: str) -> dict[str, Any]:
        day_prefix = day_iso[:10]
        with self._connect() as conn:
            trade_count = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE phase='entry' AND ts LIKE ?",
                (f"{day_prefix}%",),
            ).fetchone()[0]
            incidents = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE ts LIKE ?",
                (f"{day_prefix}%",),
            ).fetchone()[0]
            pnl = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM pnl_events WHERE ts LIKE ?",
                (f"{day_prefix}%",),
            ).fetchone()[0]

        return {
            "day": day_prefix,
            "entry_orders": int(trade_count),
            "incident_count": int(incidents),
            "realized_pnl": float(pnl),
        }
