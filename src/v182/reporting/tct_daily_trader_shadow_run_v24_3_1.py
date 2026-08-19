from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import pandas as pd

from v182.decision.tct_timing_exact_v24_1_7 import _extract_histories
from v182.features.tct_daily_trader_v24_3_1 import compute_daily_weekly_trader_snapshot


ROOT = Path(__file__).resolve().parents[3]
VERSION = "TCT_V24.3.1_DAILY_WEEKLY_TRADER_TOOLS_SHADOW"
CONFIG = "TCT_V24_3_1_DAILY_TRADER_SHADOW.json"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _completed_daily_history(history: pd.DataFrame, cfg: dict, now: datetime | None = None) -> tuple[pd.DataFrame, bool]:
    """Drop today's potentially partial daily candle before the configured local close guard."""
    if history is None or history.empty:
        return history, False
    policy = cfg["data_policy"]
    if not bool(policy.get("defer_current_day_before_local_close", True)):
        return history, False

    tz = ZoneInfo(str(policy.get("local_close_guard_timezone", "Europe/Paris")))
    current = (now or datetime.now(timezone.utc)).astimezone(tz)
    guard_hour = int(policy.get("local_close_guard_hour", 18))
    idx = pd.to_datetime(history.index, errors="coerce")
    if len(idx) == 0 or pd.isna(idx[-1]):
        return history, False
    last_date = pd.Timestamp(idx[-1]).date()
    if last_date == current.date() and current.hour < guard_hour:
        return history.iloc[:-1].copy(), True
    return history, False


def _flatten(base: pd.Series, snapshot: dict) -> dict:
    entry_components = snapshot.pop("entry_components", {}) or {}
    exit_components = snapshot.pop("exit_components", {}) or {}
    return {
        "version": VERSION,
        "isin": str(base.get("isin") or ""),
        "name": str(base.get("name") or ""),
        "yahoo_ticker": str(base.get("yahoo_ticker") or ""),
        "source_tct_decision": str(base.get("decision") or ""),
        "source_tct_setup": str(base.get("setup") or ""),
        "source_t1_quality": base.get("t1_quality_score"),
        "source_t2_quality": base.get("t2_quality_score"),
        **snapshot,
        **{f"entry_component_{k}": v for k, v in entry_components.items()},
        **{f"exit_component_{k}": v for k, v in exit_components.items()},
        "real_orders_enabled": False,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "ct_influence": 0.0,
    }


def _android_summary(frame: pd.DataFrame, generated_at: str) -> str:
    lines = [
        "# TCT V24.3.1 — Daily/Weekly Trader Tools SHADOW",
        "",
        f"Généré UTC : {generated_at}",
        "Horizon : quelques séances à environ une semaine. Pas de day trading.",
        "Données : OHLCV daily déjà collectées ; weekly dérivé du daily ; bougie du jour différée avant clôture.",
        "V24.3.1 : confluence, failed-breakout persistant, weekly complété et conflits entrée/sortie.",
        "Influence production = 0. Aucun ordre réel.",
        "",
    ]
    if frame.empty:
        lines.append("Aucun candidat TCT exploitable dans le cache quotidien.")
        return "\n".join(lines) + "\n"

    work = frame.copy()
    work["_entry"] = pd.to_numeric(work.get("entry_score"), errors="coerce")
    work["_exit"] = pd.to_numeric(work.get("exit_risk_score"), errors="coerce")
    work = work.sort_values(["_entry", "_exit"], ascending=[False, True]).head(15)
    for _, row in work.iterrows():
        name = str(row.get("name") or row.get("isin") or "N/A")
        entry = row.get("_entry")
        exit_risk = row.get("_exit")
        entry_txt = "N/A" if pd.isna(entry) else f"{float(entry):.1f}"
        exit_txt = "N/A" if pd.isna(exit_risk) else f"{float(exit_risk):.1f}"
        confirmations = row.get("entry_confirmation_count")
        conf_txt = "N/A" if pd.isna(confirmations) else str(int(confirmations))
        lines.append(
            f"- **{name}** — entrée {row.get('entry_state')} ({entry_txt}; conf. {conf_txt}) — sortie {row.get('exit_state')} ({exit_txt})"
        )
        reasons = str(row.get("entry_reasons") or "").strip()
        warnings = str(row.get("warnings") or "").strip()
        if reasons and reasons.lower() != "nan":
            lines.append(f"  - Confirmations : {reasons}")
        if warnings and warnings.lower() != "nan":
            lines.append(f"  - Warnings : {warnings}")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    outdir = root / "outputs" / "daily_tct_ct"
    auditdir = root / "outputs" / "audit"
    mobile = root / "outputs" / "mobile"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    tct = _read_csv(outdir / "TCT_SHADOW_V24_1_7.csv")
    actions = _read_csv(root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    deferred_current_day = 0
    if tct.empty or actions.empty:
        output = pd.DataFrame()
        histories_found = 0
        errors = ["TCT_OR_ACTION_MASTER_MISSING"]
    else:
        ticker_col = "yahoo_ticker" if "yahoo_ticker" in actions.columns else "ticker"
        mapping = actions[["isin", ticker_col]].copy()
        mapping["isin"] = mapping["isin"].astype(str).str.upper()
        mapping[ticker_col] = mapping[ticker_col].astype(str).str.strip()
        work = tct.copy()
        work["isin"] = work["isin"].astype(str).str.upper()
        if ticker_col not in work.columns:
            work = work.merge(mapping.drop_duplicates("isin"), on="isin", how="left")
        else:
            mapped = mapping.drop_duplicates("isin").rename(columns={ticker_col: "_mapped_ticker"})
            work = work.merge(mapped, on="isin", how="left")
            work[ticker_col] = work[ticker_col].where(
                work[ticker_col].notna() & ~work[ticker_col].astype(str).isin({"", "nan", "None"}),
                work["_mapped_ticker"],
            )
            work = work.drop(columns=["_mapped_ticker"], errors="ignore")
        work = work[work[ticker_col].notna() & ~work[ticker_col].astype(str).isin({"", "nan", "None"})].copy()
        work = work.rename(columns={ticker_col: "yahoo_ticker"}) if ticker_col != "yahoo_ticker" else work
        tickers = set(work["yahoo_ticker"].astype(str))
        histories = _extract_histories(root / cfg["data_policy"]["source_cache"], tickers)
        histories_found = len(histories)
        rows: list[dict] = []
        errors: list[str] = []
        for _, row in work.iterrows():
            ticker = str(row["yahoo_ticker"]).strip()
            history = histories.get(ticker)
            if history is None or history.empty:
                snap = {
                    "status": "DATA_INSUFFICIENT",
                    "bars": 0,
                    "intraday_data_used": False,
                    "new_market_data_downloads_required": False,
                    "decision_influence": 0.0,
                    "score_influence": 0.0,
                    "sizing_influence": 0.0,
                    "stop_loss_influence": 0.0,
                }
            else:
                history, deferred = _completed_daily_history(history, cfg)
                deferred_current_day += int(deferred)
                try:
                    snap = compute_daily_weekly_trader_snapshot(history, cfg)
                except Exception as exc:
                    errors.append(f"{ticker}:{type(exc).__name__}:{str(exc)[:160]}")
                    snap = {
                        "status": "ERROR_SHADOW",
                        "bars": int(len(history)),
                        "intraday_data_used": False,
                        "new_market_data_downloads_required": False,
                        "decision_influence": 0.0,
                        "score_influence": 0.0,
                        "sizing_influence": 0.0,
                        "stop_loss_influence": 0.0,
                    }
            rows.append(_flatten(row, snap.copy()))
        output = pd.DataFrame(rows)

    output_path = outdir / "TCT_DAILY_TRADER_V24_3_1_SHADOW.csv"
    _write_csv(output, output_path)
    android_path = mobile / "ANDROID_TCT_DAILY_TRADER_SHADOW.md"
    android_path.write_text(_android_summary(output, generated_at), encoding="utf-8")

    payload = {
        "status": "SUCCESS_SHADOW" if not errors else "SUCCESS_SHADOW_WITH_WARNINGS",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "rows": int(len(output)),
        "daily_histories_found": int(histories_found),
        "current_day_candles_deferred": int(deferred_current_day),
        "completed_daily_bars_only": True,
        "daily_ohlcv_only": True,
        "weekly_derived_from_daily": True,
        "intraday_data_used": False,
        "five_minute_data_used": False,
        "quasi_realtime_data_used": False,
        "new_market_data_downloads_required": False,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_influence": 0.0,
        "stop_loss_influence": 0.0,
        "ct_influence": 0.0,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "errors": errors[:50],
        "outputs": {
            "shadow": str(output_path.relative_to(root)),
            "android": str(android_path.relative_to(root)),
        },
    }
    (auditdir / "TCT_DAILY_TRADER_V24_3_1_AUDIT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
