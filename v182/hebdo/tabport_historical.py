"""Runner historique TABPORT 65 k€ sur signaux PIT et OHLC réels.

Aucune génération, interpolation ou donnée synthétique. Le runner bloque si les
fichiers, la couverture demandée ou la provenance PIT ligne par ligne sont insuffisants.
Il accepte aussi directement le cache OHLCV gouverné en blocs Parquet wide
(Date en index, colonnes MultiIndex ticker/champ) et le normalise en OHLCV long.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from v182.audit.pit_loader import PITLoader
from v182.hebdo.tabport import Tabport65k, TabportConfig


OHLC_FIELDS = {"open", "high", "low", "close"}
VOLUME_FIELD = "volume"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        files = sorted(
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in {".csv", ".parquet", ".pq"}
        )
        if files:
            return files
    raise ValueError(f"BLOCK_DATA_HISTORICAL: missing/empty input {path}")


def _sha256_source(path: Path) -> str:
    files = _source_files(path)
    if len(files) == 1 and files[0] == path:
        return _sha256(path)
    h = hashlib.sha256()
    for p in files:
        rel = str(p.relative_to(path)).replace("\\", "/")
        h.update(rel.encode("utf-8")); h.update(b"\0")
        h.update(_sha256(p).encode("ascii")); h.update(b"\n")
    return h.hexdigest()


def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: missing/empty input {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"BLOCK_DATA_HISTORICAL: unsupported input format {suffix}")


def _tuple_columns_if_encoded(df: pd.DataFrame) -> pd.DataFrame:
    """Recover tuple columns only when every column is an encoded 2-tuple."""
    if isinstance(df.columns, pd.MultiIndex) or len(df.columns) == 0:
        return df
    parsed = []
    for c in df.columns:
        if not isinstance(c, str) or not c.startswith("("):
            return df
        try:
            value = ast.literal_eval(c)
        except (ValueError, SyntaxError):
            return df
        if not isinstance(value, tuple) or len(value) != 2:
            return df
        parsed.append(value)
    out = df.copy()
    out.columns = pd.MultiIndex.from_tuples(parsed)
    return out


def _wide_ohlc_to_long(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """Normalize governed cache blocks to date/ticker/OHLC plus volume when present."""
    lower = {str(c).lower(): c for c in df.columns}
    if {"date", "ticker", *OHLC_FIELDS}.issubset(lower):
        names = ["date", "ticker", "open", "high", "low", "close"]
        if VOLUME_FIELD in lower:
            names.append(VOLUME_FIELD)
        rename = {lower[k]: k for k in names}
        return df.rename(columns=rename)[names].copy()

    work = _tuple_columns_if_encoded(df)
    if not isinstance(work.columns, pd.MultiIndex) or work.columns.nlevels != 2:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {source_name} unsupported OHLC layout")

    level0 = {str(x).strip().lower() for x in work.columns.get_level_values(0)}
    level1 = {str(x).strip().lower() for x in work.columns.get_level_values(1)}
    if OHLC_FIELDS.issubset(level1):
        ticker_level, field_level = 0, 1
    elif OHLC_FIELDS.issubset(level0):
        ticker_level, field_level = 1, 0
    else:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {source_name} missing OHLC fields in wide cache")

    dates = pd.to_datetime(work.index, errors="coerce", utc=True)
    if len(dates) == 0 or dates.isna().any():
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {source_name} invalid Date index")

    tickers = pd.Index(work.columns.get_level_values(ticker_level)).unique()
    pieces = []
    for ticker in tickers:
        cols = [c for c in work.columns if c[ticker_level] == ticker]
        by_field = {str(c[field_level]).strip().lower(): c for c in cols}
        if not OHLC_FIELDS.issubset(by_field):
            continue
        data = {
            "date": dates,
            "ticker": str(ticker).strip().upper(),
            "open": work[by_field["open"]].to_numpy(),
            "high": work[by_field["high"]].to_numpy(),
            "low": work[by_field["low"]].to_numpy(),
            "close": work[by_field["close"]].to_numpy(),
        }
        if VOLUME_FIELD in by_field:
            data[VOLUME_FIELD] = work[by_field[VOLUME_FIELD]].to_numpy()
        part = pd.DataFrame(data)
        # Les caches de marché peuvent contenir des lignes NaN avant cotation ou
        # après radiation. Elles ne sont pas synthétisées: elles sont simplement absentes.
        part = part.dropna(subset=["open", "high", "low", "close"], how="any")
        if not part.empty:
            pieces.append(part)
    if not pieces:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {source_name} contains no usable OHLC bars")
    return pd.concat(pieces, ignore_index=True)


def _read_ohlc_source(path: Path) -> tuple[pd.DataFrame, list[str]]:
    files = _source_files(path)
    parts = []
    used = []
    for p in files:
        raw = _read_table(p)
        parts.append(_wide_ohlc_to_long(raw, str(p)))
        used.append(str(p))
    out = pd.concat(parts, ignore_index=True)
    if out.duplicated(["date", "ticker"]).any():
        dup = out.loc[out.duplicated(["date", "ticker"], keep=False), ["date", "ticker"]].head(1)
        raise ValueError(f"BLOCK_DATA_HISTORICAL: duplicate OHLC across cache blocks {dup.to_dict('records')}")
    return out, used


def _as_utc(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _date_bounds(df: pd.DataFrame, name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    if "date" not in df.columns:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {name} missing date")
    dates = pd.to_datetime(df["date"], errors="coerce", utc=True)
    if dates.isna().any() or dates.empty:
        raise ValueError(f"BLOCK_DATA_HISTORICAL: {name} invalid dates")
    return dates.min(), dates.max()


def _validate_row_level_pit(signals: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    if "pit_snapshot_time" not in signals.columns:
        raise ValueError("BLOCK_DATA_HISTORICAL: signals missing pit_snapshot_time")
    signal_dates = pd.to_datetime(signals["date"], errors="coerce", utc=True)
    snapshots = pd.to_datetime(signals["pit_snapshot_time"], errors="coerce", utc=True)
    if signal_dates.isna().any() or snapshots.isna().any():
        raise ValueError("BLOCK_DATA_HISTORICAL: invalid signal/PIT timestamps")
    loader = PITLoader(strict_provenance=True)
    bad = []
    for idx, (decision, snapshot) in enumerate(zip(signal_dates, snapshots)):
        cutoff = loader.cutoff_for(decision)
        snapshot_paris = snapshot.tz_convert(loader.paris_tz)
        if snapshot_paris > cutoff:
            bad.append((idx, snapshot_paris.isoformat(), cutoff.isoformat()))
    if bad:
        idx, snapshot, cutoff = bad[0]
        raise ValueError(
            f"BLOCK_DATA_HISTORICAL: PIT look-ahead row={idx} snapshot={snapshot} cutoff={cutoff}"
        )
    return snapshots.min(), snapshots.max()


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
    start_ts, end_ts = _as_utc(start), _as_utc(end)
    if end_ts < start_ts:
        raise ValueError("BLOCK_DATA_HISTORICAL: end before start")

    signals = _read_table(signals_path)
    ohlc, ohlc_files = _read_ohlc_source(ohlc_path)
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

    pit_min, pit_max = _validate_row_level_pit(signals)

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
            "signals": {"path": str(signals_path), "sha256": _sha256_source(signals_path), "rows": int(len(signals)),
                        "source_min_date": str(sig_min), "source_max_date": str(sig_max),
                        "pit_snapshot_min": str(pit_min), "pit_snapshot_max": str(pit_max),
                        "pit_validation": "ROW_LEVEL_T_MINUS_1_22H_EUROPE_PARIS"},
            "ohlc": {"path": str(ohlc_path), "sha256": _sha256_source(ohlc_path), "rows": int(len(ohlc)),
                     "source_min_date": str(px_min), "source_max_date": str(px_max),
                     "source_files": ohlc_files, "layout_normalization": "LONG_OR_GOVERNED_WIDE_CACHE"},
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
    p.add_argument("--ohlc", required=True, help="OHLC/OHLCV long file or governed cache directory")
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
