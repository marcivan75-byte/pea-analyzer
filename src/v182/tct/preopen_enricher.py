from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
import yfinance as yf


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_REL = Path("outputs/daily_tct_ct/TCT_PREOPEN_ENRICHED_ACTIVE.csv")
AUDIT_REL = Path("outputs/audit/TCT_PREOPEN_ENRICHER_ACTIVE_AUDIT.json")
CANDIDATE_STATE_REL = Path("state/tct_context/TCT_CT_PREOPEN_CANDIDATES_LATEST.csv")
CANDIDATE_PATHS = (
    Path("outputs/daily_tct_ct/DAILY_TCT_CT_DECISIONS.csv"),
    Path("outputs/committee_master/CI_DAILY_DECISIONS.csv"),
)
SCORE_COLUMNS = ("final_score", "decision_score", "score", "quality_score", "composite_score")
TICKER_COLUMNS = ("ticker", "symbol", "yahoo_ticker")


class PreopenBlocked(RuntimeError):
    pass


@dataclass
class FinnhubClient:
    api_key: str | None
    disabled: bool = False

    def company_news_last_12h(self, ticker: str, now_utc: datetime) -> tuple[int, str]:
        if not self.api_key or self.disabled:
            return 0, "UNAVAILABLE"
        start = now_utc - timedelta(hours=12)
        params = {
            "symbol": ticker,
            "from": start.date().isoformat(),
            "to": now_utc.date().isoformat(),
            "token": self.api_key,
        }
        try:
            response = requests.get("https://finnhub.io/api/v1/company-news", params=params, timeout=8)
        except requests.RequestException:
            return 0, "SOURCE_ERROR"
        if response.status_code in {401, 403}:
            self.disabled = True
            return 0, f"HTTP_{response.status_code}_SOURCE_DISABLED"
        if response.status_code != 200:
            return 0, f"HTTP_{response.status_code}"
        try:
            rows = response.json()
        except ValueError:
            return 0, "INVALID_JSON"
        if not isinstance(rows, list):
            return 0, "INVALID_PAYLOAD"
        cutoff = int(start.timestamp())
        recent = [row for row in rows if int(row.get("datetime") or 0) >= cutoff]
        return len(recent), "OK"


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, sep=None, engine="python", low_memory=False)
    except TypeError:
        return pd.read_csv(path, sep=None, engine="python")


def _first_existing_column(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lower = {str(col).lower(): str(col) for col in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def _bound_candidates(frame: pd.DataFrame, max_tct: int, max_ct: int) -> pd.DataFrame:
    horizon_col = _first_existing_column(frame, ("horizon", "time_horizon"))
    ticker_col = _first_existing_column(frame, TICKER_COLUMNS)
    isin_col = _first_existing_column(frame, ("isin",))
    if horizon_col is None:
        raise PreopenBlocked("BLOCK_DATA: preselection has no horizon column")
    if ticker_col is None:
        raise PreopenBlocked("BLOCK_DATA: preselection has no validated ticker column")

    rows = frame.copy()
    rows["_horizon"] = rows[horizon_col].astype(str).str.upper().str.strip()
    rows["_ticker"] = rows[ticker_col].astype(str).str.upper().str.strip()
    rows = rows[rows["_ticker"].ne("") & rows["_ticker"].ne("NAN")].copy()
    if rows.empty:
        raise PreopenBlocked("BLOCK_DATA: no validated ticker in preselection")

    score_col = _first_existing_column(rows, SCORE_COLUMNS)
    if score_col is not None:
        rows["_sort_score"] = pd.to_numeric(rows[score_col], errors="coerce").fillna(float("-inf"))
        rows = rows.sort_values("_sort_score", ascending=False, kind="stable")

    tct = rows[rows["_horizon"].eq("TCT")].head(max_tct)
    ct = rows[rows["_horizon"].eq("CT")].head(max_ct)
    bounded = pd.concat([tct, ct], ignore_index=True, sort=False)
    if bounded.empty:
        raise PreopenBlocked("BLOCK_DATA: no TCT/CT rows after bounded selection")

    dedupe_key = isin_col if isin_col is not None else "_ticker"
    bounded = bounded.drop_duplicates(subset=[dedupe_key], keep="first").head(max_tct + max_ct)
    if len(bounded) > 40:
        raise AssertionError("Preopen universe must remain <= 40")
    return bounded


def prepare_candidates(root: Path, max_tct: int = 20, max_ct: int = 20) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for rel in CANDIDATE_PATHS:
        path = root / rel
        if path.is_file():
            frame = _read_csv(path)
            frame["_candidate_source"] = str(rel)
            frames.append(frame)
    if not frames:
        raise PreopenBlocked("BLOCK_DATA: no TCT/CT preselection file available")
    bounded = _bound_candidates(pd.concat(frames, ignore_index=True, sort=False), max_tct, max_ct)
    state_path = root / CANDIDATE_STATE_REL
    state_path.parent.mkdir(parents=True, exist_ok=True)
    bounded.to_csv(state_path, index=False, encoding="utf-8-sig")
    return bounded


def _load_candidates(root: Path, max_tct: int, max_ct: int) -> pd.DataFrame:
    state_path = root / CANDIDATE_STATE_REL
    if state_path.is_file():
        return _bound_candidates(_read_csv(state_path), max_tct, max_ct)
    return prepare_candidates(root, max_tct=max_tct, max_ct=max_ct)


def _premarket_snapshot(ticker: str) -> dict[str, object]:
    result: dict[str, object] = {
        "gap_overnight": None,
        "premarket_volume": None,
        "preopen_price": None,
        "previous_close": None,
        "market_source_status": "BLOCK_DATA",
    }
    try:
        instrument = yf.Ticker(ticker)
        fast = instrument.fast_info
        previous_close = float(fast.get("previous_close")) if fast.get("previous_close") is not None else None
        intraday = instrument.history(period="1d", interval="5m", prepost=True, auto_adjust=False)
    except Exception as exc:
        result["market_source_status"] = f"SOURCE_ERROR:{type(exc).__name__}"
        return result

    if previous_close is None or previous_close <= 0 or intraday.empty:
        return result

    open_col = "Open" if "Open" in intraday.columns else "open" if "open" in intraday.columns else None
    volume_col = "Volume" if "Volume" in intraday.columns else "volume" if "volume" in intraday.columns else None
    if open_col is None or volume_col is None:
        return result

    opens = pd.to_numeric(intraday[open_col], errors="coerce").dropna()
    volumes = pd.to_numeric(intraday[volume_col], errors="coerce").fillna(0.0)
    if opens.empty:
        return result

    preopen_price = float(opens.iloc[0])
    result.update(
        {
            "gap_overnight": preopen_price / previous_close - 1.0,
            "premarket_volume": float(volumes.sum()),
            "preopen_price": preopen_price,
            "previous_close": previous_close,
            "market_source_status": "OK",
        }
    )
    return result


def run(
    root: Path = DEFAULT_ROOT,
    *,
    max_tct: int = 20,
    max_ct: int = 20,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    if max_tct < 0 or max_ct < 0 or max_tct + max_ct > 40:
        raise ValueError("TCT + CT preopen cap must be <= 40")
    root = Path(root).resolve()
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    candidates = _load_candidates(root, max_tct=max_tct, max_ct=max_ct)
    finnhub = FinnhubClient(os.getenv("FINNHUB_API_KEY"))
    enriched: list[dict[str, object]] = []

    for _, row in candidates.iterrows():
        ticker = str(row["_ticker"])
        market = _premarket_snapshot(ticker)
        news_count, news_status = finnhub.company_news_last_12h(ticker, now_utc)
        record = row.to_dict()
        record.update(market)
        record["news_finnhub_12h_count"] = int(news_count)
        record["news_finnhub_status"] = news_status
        record["preopen_enrichment_status"] = (
            "ACTIVE_OK"
            if market["market_source_status"] == "OK" and news_status == "OK"
            else "ACTIVE_BLOCK_DATA_PARTIAL"
        )
        record["generated_at_utc"] = now_utc.isoformat()
        enriched.append(record)

    output = pd.DataFrame(enriched)
    out_path = root / OUTPUT_REL
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(out_path, index=False, encoding="utf-8-sig")

    audit = {
        "status": "ACTIVE",
        "mode": "PREOPEN",
        "universe_policy": "UNION_DEDUP_20_TCT_PLUS_20_CT_MAX_40",
        "candidate_rows": int(len(output)),
        "tct_rows": int((output["_horizon"] == "TCT").sum()),
        "ct_rows": int((output["_horizon"] == "CT").sum()),
        "finnhub_disabled_after_auth_error": bool(finnhub.disabled),
        "fail_closed_if_preselection_missing": True,
        "real_orders_enabled": False,
        "candidate_state": str(CANDIDATE_STATE_REL),
        "output": str(OUTPUT_REL),
        "generated_at_utc": now_utc.isoformat(),
    }
    audit_path = root / AUDIT_REL
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def _write_block_audit(root: Path, error: str) -> None:
    audit_path = root / AUDIT_REL
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps({"status": "BLOCK_DATA", "error": error, "real_orders_enabled": False}, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--max-tct", type=int, default=20)
    parser.add_argument("--max-ct", type=int, default=20)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--active", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    daily_profile = os.getenv("PEA_RUN_PROFILE", "").strip().upper() == "DAILY_TACTICAL"
    prepare_mode = args.prepare_only or (daily_profile and not args.active)
    try:
        if prepare_mode:
            candidates = prepare_candidates(root, max_tct=args.max_tct, max_ct=args.max_ct)
            payload = {
                "status": "PREPARED",
                "mode": "NEXT_PREOPEN_CANDIDATE_STATE",
                "candidate_rows": int(len(candidates)),
                "candidate_state": str(CANDIDATE_STATE_REL),
                "network_enrichment_calls": 0,
                "real_orders_enabled": False,
            }
        else:
            payload = run(root, max_tct=args.max_tct, max_ct=args.max_ct)
    except PreopenBlocked as exc:
        _write_block_audit(root, str(exc))
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
