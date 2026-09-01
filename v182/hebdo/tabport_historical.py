"""Runner historique TABPORT 65 k€ sur signaux PIT et OHLC réels.

Aucune génération, interpolation ou donnée synthétique. Le runner bloque si les
fichiers, la couverture demandée ou la provenance minimale sont insuffisants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: missing/empty input {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"BLOCK_DATA_HISTORICAL: unsupported input format {suffix}")


def _date_bounds(df: pd.DataFrame, name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    if "date" not in df.columns:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {name} missing date")
    dates = pd.to_datetime(df["date"], errors="coerce", utc=True)
    if dates.isna().any() or dates.empty:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {name} invalid dates")
    return dates.min(), dates.max()


def run_historical(
    signals_path: str | Path,
    ohlc_path: str | Path,
    start: str,
    end: str,
    output_dir: str | Path,
    config: TabportConfig | None = None,
) -> dict:
    signals_path, ohlc_path = Path(signals_path), Path(ohlc_path)
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    if end_ts < start_ts:
        raise ValueError("BLOCK_DATA_HISTORICAL: end before start")

    signals = _read_table(signals_path)
    ohlc = _read_table(ohlc_path)
    sig_min, sig_max = _date_bounds(signals, "signals")
    px_min, px_max = _date_bounds(ohlc, "ohlc")

    sig_dates = pd.to_datetime(signals["date"], errors="coerce", utc=True)
    px_dates = pd.to_datetime(ohlc["date"], errors="coerce", utc=True)
    signals = signals.loc[(sig_dates >= start_ts) & (sig_dates <= end_ts)].copy()
    ohlc = ohlc.loc[(px_dates >= start_ts) & (px_dates <= end_ts)].copy()
    if signals.empty:
        raise ValueError("BLOCK_DATA_HISTORICAL: no signals in requested window")
    if ohlc.empty:
        raise ValueError("BLOCK_DATA_HISTORICAL: no OHLC in requested window")

    # Fail closed: les prix doivent couvrir au moins la première et la dernière
    # date de signal retenue. TABPORT contrôle ensuite J+1 et chaque barre utile.
    kept_sig_dates = pd.to_datetime(signals["date"], utc=True)
    kept_px_dates = pd.to_datetime(ohlc["date"], utc=True)
    if kept_px_dates.min() > kept_sig_dates.min() or kept_px_dates.max() <= kept_sig_dates.max():
        raise ValueError("BLOCK_DATA_HISTORICAL: OHLC do not span retained signal dates/J+1")

    cfg = config or TabportConfig()
    result = Tabport65k(cfg).run(signals, ohlc)
    result["ledger"].to_csv(out / "TABPORT_LEDGER.csv", index=False)
    result["equity"].to_csv(out / "TABPORT_DAILY_NAV.csv", index=False)
    result["quarterly"].to_csv(out / "TABPORT_QUARTERLY.csv", index=False)
    result["yearly"].to_csv(out / "TABPORT_YEARLY.csv", index=False)
    result["skipped"].to_csv(out / "TABPORT_SKIPPED.csv", index=False)

    manifest = {
        "status": "OK",
        "engine": "TABPORT_HEBDO_AT_META",
        "window": {"start": str(start_ts), "end": str(end_ts)},
        "inputs": {
            "signals": {"path": str(signals_path), "sha256": _sha256(signals_path), "rows": int(len(signals)),
                        "source_min_date": str(sig_min), "source_max_date": str(sig_max)},
            "ohlc": {"path": str(ohlc_path), "sha256": _sha256(ohlc_path), "rows": int(len(ohlc)),
                     "source_min_date": str(px_min), "source_max_date": str(px_max)},
        },
        "config": asdict(cfg),
        "metrics": result["metrics"],
        "synthetic_fallback": False,
        "retuning": False,
    }
    (out / "TABPORT_MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return {**result, "manifest": manifest}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--signals", required=True)
    p.add_argument("--ohlc", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--output-dir", default="outputs/tabport_historical")
    args = p.parse_args()
    try:
        result = run_historical(args.signals, args.ohlc, args.start, args.end, args.output_dir)
    except Exception as exc:
        out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
        block = {"status": "BLOCK_DATA_HISTORICAL", "reason": str(exc), "synthetic_fallback": False}
        (out / "TABPORT_BLOCK.json").write_text(json.dumps(block, indent=2), encoding="utf-8")
        print(json.dumps(block)); raise SystemExit(2)
    print(json.dumps(result["manifest"], default=str))


if __name__ == "__main__":
    main()
