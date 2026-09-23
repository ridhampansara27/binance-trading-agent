from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import pandas as pd

from trend_scalper.research.backtester import ResearchConfig, run_research_backtest
from trend_scalper.research.downloader import download_futures_klines, save_research_data


def _load(symbol: str, interval: str, data_dir: str) -> pd.DataFrame:
    return pd.read_parquet(Path(data_dir) / f"{symbol.lower()}_{interval}.parquet")


def cmd_download(days: int, data_dir: str) -> None:
    for symbol, interval in itertools.product(["BTCUSDT", "ETHUSDT", "SOLUSDT"], ["1m", "5m"]):
        df = download_futures_klines(symbol, interval, days=days)
        out = save_research_data(df, symbol, interval, data_dir)
        print(f"saved {len(df)} rows -> {out}")


def cmd_backtest(data_dir: str, output: str, split: float, stress_mult: float) -> None:
    cfg = ResearchConfig(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        correlation_groups=[["BTCUSDT", "ETHUSDT", "SOLUSDT"]],
        min_notional={"BTCUSDT": 5, "ETHUSDT": 5, "SOLUSDT": 5},
        min_qty={"BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1},
        step_size={"BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1},
        fee_taker=0.0005 * stress_mult,
        slippage_bps=2 * stress_mult,
    )

    bars_5m = {s: _load(s, "5m", data_dir) for s in cfg.symbols}
    bars_1m = {s: _load(s, "1m", data_dir) for s in cfg.symbols}
    for symbol in cfg.symbols:
        bars_5m[symbol]["timestamp"] = pd.to_datetime(bars_5m[symbol]["timestamp"], utc=True)
        bars_1m[symbol]["timestamp"] = pd.to_datetime(bars_1m[symbol]["timestamp"], utc=True)

    split_index = int(len(bars_5m["BTCUSDT"]) * split)
    in_sample_5m = {s: df.iloc[:split_index].reset_index(drop=True) for s, df in bars_5m.items()}
    out_sample_5m = {s: df.iloc[split_index:].reset_index(drop=True) for s, df in bars_5m.items()}
    split_ts = bars_5m["BTCUSDT"].iloc[split_index]["timestamp"]
    in_sample_1m = {s: df[df["timestamp"] < split_ts].reset_index(drop=True) for s, df in bars_1m.items()}
    out_sample_1m = {s: df[df["timestamp"] >= split_ts].reset_index(drop=True) for s, df in bars_1m.items()}

    param_grid = [
        {"entry_zscore": ez, "take_profit_pct": tp, "stop_loss_pct": sl}
        for ez in [1.5, 2.0, 2.5]
        for tp in [0.003, 0.004, 0.005]
        for sl in [0.002, 0.003]
    ]
    in_sample_reports = []
    for params in param_grid:
        this_cfg = cfg
        this_cfg.entry_zscore = params["entry_zscore"]
        this_cfg.take_profit_pct = params["take_profit_pct"]
        this_cfg.stop_loss_pct = params["stop_loss_pct"]
        result = run_research_backtest(in_sample_5m, in_sample_1m, this_cfg)
        in_sample_reports.append({"params": params, "summary": result["summary"]})

    best = max(in_sample_reports, key=lambda x: x["summary"].get("net_return", -999))
    cfg.entry_zscore = best["params"]["entry_zscore"]
    cfg.take_profit_pct = best["params"]["take_profit_pct"]
    cfg.stop_loss_pct = best["params"]["stop_loss_pct"]

    out_result = run_research_backtest(out_sample_5m, out_sample_1m, cfg)
    report = {"in_sample_grid": in_sample_reports, "selected_params": best["params"], "out_of_sample": out_result}
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"saved report -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="1m/5m research runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    download = sub.add_parser("download")
    download.add_argument("--days", type=int, default=90)
    download.add_argument("--data-dir", default="data/research")
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--data-dir", default="data/research")
    backtest.add_argument("--output", default="results/research_5m_report.json")
    backtest.add_argument("--split", type=float, default=0.7)
    backtest.add_argument("--stress-mult", type=float, default=1.0)
    args = parser.parse_args()
    if args.cmd == "download":
        cmd_download(args.days, args.data_dir)
    elif args.cmd == "backtest":
        cmd_backtest(args.data_dir, args.output, args.split, args.stress_mult)


if __name__ == "__main__":
    main()
