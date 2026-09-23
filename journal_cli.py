"""Journal utility commands."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from journal import TradeJournal


def main() -> None:
    parser = argparse.ArgumentParser(description="Trade journal utilities")
    parser.add_argument("--db", default="data/trade_journal.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export-csv")
    export.add_argument("--table", required=True, choices=["signals", "orders", "positions", "incidents", "pnl_events"])
    export.add_argument("--output", required=True)

    summary = sub.add_parser("daily-summary")
    summary.add_argument("--day", default=datetime.now(tz=UTC).date().isoformat())

    args = parser.parse_args()
    journal = TradeJournal(args.db)
    if args.command == "export-csv":
        journal.export_table_csv(args.table, args.output)
        print(json.dumps({"ok": True, "table": args.table, "output": args.output}))
    elif args.command == "daily-summary":
        print(json.dumps(journal.daily_summary(args.day), indent=2))


if __name__ == "__main__":
    main()
