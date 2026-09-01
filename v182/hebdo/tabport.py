"""TABPORT HEBDO AT META - simulateur de portefeuille gouverné, sans données synthétiques.

Contrat d'entrée
----------------
signals : DataFrame contenant au minimum ``date``, ``ticker`` et ``EV_net``.
          Si ``tier`` est présent, seuls TCT/CT_WATCH sont éligibles par défaut.
prices  : DataFrame journalier contenant ``date``, ``ticker``, ``open``, ``high``,
          ``low`` et ``close``.

La sélection est effectuée à la date du signal par EV_net décroissante, puis l'entrée
est exécutée à l'ouverture de la première séance strictement postérieure (J+1 marché).
Aucun accès à une barre future n'est utilisé pour classer les candidats.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TabportConfig:
    initial_cash: float = 65_000.0
    max_positions: int = 12
    max_position_eur: float = 4_500.0
    max_entries_month: int = 5
    max_entries_year: int = 40
    fee_rate: float = 0.002
    slippage_rate: float = 0.001
    stop_pct: float = 0.09
    max_hold_sessions: int = 126
    allowed_tiers: tuple[str, ...] = ("TCT", "CT_WATCH")

    def validate(self) -> None:
        if self.initial_cash <= 0 or self.max_position_eur <= 0:
            raise ValueError("BLOCK_DATA_TABPORT: capital/budget must be positive")
        if self.max_positions < 1 or self.max_entries_month < 1 or self.max_entries_year < 1:
            raise ValueError("BLOCK_DATA_TABPORT: capacity limits must be >= 1")
        if not (0 <= self.fee_rate < 0.1 and 0 <= self.slippage_rate < 0.1):
            raise ValueError("BLOCK_DATA_TABPORT: invalid fee/slippage")
        if not (0 < self.stop_pct < 1) or self.max_hold_sessions < 1:
            raise ValueError("BLOCK_DATA_TABPORT: invalid stop/holding horizon")


class Tabport65k:
    def __init__(self, config: TabportConfig | None = None):
        self.cfg = config or TabportConfig()
        self.cfg.validate()

    @staticmethod
    def _normalize_signals(signals: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "ticker", "EV_net"}
        missing = required - set(signals.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_TABPORT: signals missing {sorted(missing)}")
        s = signals.copy()
        s["date"] = pd.to_datetime(s["date"], errors="coerce", utc=True)
        s["ticker"] = s["ticker"].astype(str).str.strip().str.upper()
        s["EV_net"] = pd.to_numeric(s["EV_net"], errors="coerce")
        bad = s["date"].isna() | s["ticker"].isin(["", "NAN", "NONE"]) | ~np.isfinite(s["EV_net"])
        if bad.any():
            raise ValueError("BLOCK_DATA_TABPORT: invalid signal date/ticker/EV_net")
        if s.duplicated(["date", "ticker"]).any():
            raise ValueError("BLOCK_DATA_TABPORT: duplicate ticker signal on same date")
        return s.sort_values(["date", "EV_net", "ticker"], ascending=[True, False, True]).reset_index(drop=True)

    @staticmethod
    def _normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "ticker", "open", "high", "low", "close"}
        missing = required - set(prices.columns)
        if missing:
            raise ValueError(f"BLOCK_DATA_TABPORT: prices missing {sorted(missing)}")
        p = prices.copy()
        p["date"] = pd.to_datetime(p["date"], errors="coerce", utc=True)
        p["ticker"] = p["ticker"].astype(str).str.strip().str.upper()
        for c in ["open", "high", "low", "close"]:
            p[c] = pd.to_numeric(p[c], errors="coerce")
        arr = p[["open", "high", "low", "close"]].to_numpy(dtype=float)
        bad = p["date"].isna() | p["ticker"].isin(["", "NAN", "NONE"]) | ~np.isfinite(arr).all(axis=1)
        bad |= (p[["open", "high", "low", "close"]] <= 0).any(axis=1)
        bad |= (p["low"] > p["high"]) | (p["open"] < p["low"]) | (p["open"] > p["high"]) | (p["close"] < p["low"]) | (p["close"] > p["high"])
        if bad.any():
            raise ValueError("BLOCK_DATA_TABPORT: invalid OHLC/date/ticker")
        if p.duplicated(["date", "ticker"]).any():
            raise ValueError("BLOCK_DATA_TABPORT: duplicate price bar")
        return p.sort_values(["date", "ticker"]).reset_index(drop=True)

    def run(self, signals: pd.DataFrame, prices: pd.DataFrame) -> dict[str, pd.DataFrame | dict]:
        s = self._normalize_signals(signals)
        p = self._normalize_prices(prices)
        if s.empty or p.empty:
            raise ValueError("BLOCK_DATA_TABPORT: empty signals/prices")

        if "tier" in s.columns:
            s = s[s["tier"].isin(self.cfg.allowed_tiers)].copy()
        s = s[s["EV_net"] >= 0].copy()
        if s.empty:
            raise ValueError("BLOCK_DATA_TABPORT: no eligible non-negative-EV signals")

        price_dates = p.groupby("ticker")["date"].apply(list).to_dict()
        scheduled: dict[pd.Timestamp, list[dict]] = {}
        skipped = []
        for _, row in s.iterrows():
            dates = price_dates.get(row["ticker"], [])
            nxt = next((d for d in dates if d > row["date"]), None)
            if nxt is None:
                skipped.append({"signal_date": row["date"], "ticker": row["ticker"], "reason": "NO_J1_BAR"})
                continue
            scheduled.setdefault(nxt, []).append(row.to_dict())

        bars_by_date = {d: g.set_index("ticker") for d, g in p.groupby("date", sort=True)}
        all_dates = sorted(bars_by_date)
        cash = float(self.cfg.initial_cash)
        positions: dict[str, dict] = {}
        ledger: list[dict] = []
        equity_rows: list[dict] = []
        entries_month: dict[tuple[int, int], int] = {}
        entries_year: dict[int, int] = {}

        def close_position(ticker: str, date: pd.Timestamp, bar: pd.Series, reason: str, raw_exit: float) -> None:
            nonlocal cash
            pos = positions.pop(ticker)
            sell_px = float(raw_exit) * (1 - self.cfg.slippage_rate)
            gross = sell_px * pos["shares"]
            sell_fee = gross * self.cfg.fee_rate
            cash += gross - sell_fee
            pnl_net = (gross - sell_fee) - pos["cash_out"]
            ret_net = pnl_net / pos["cash_out"]
            ledger.append({
                "ticker": ticker,
                "signal_date": pos["signal_date"],
                "entry_date": pos["entry_date"],
                "exit_date": date,
                "shares": pos["shares"],
                "entry_price": pos["entry_price"],
                "exit_price": sell_px,
                "entry_fee": pos["entry_fee"],
                "exit_fee": sell_fee,
                "fees_total": pos["entry_fee"] + sell_fee,
                "slippage_rate_side": self.cfg.slippage_rate,
                "cash_invested": pos["cash_out"],
                "pnl_net": pnl_net,
                "return_net": ret_net,
                "exit_reason": reason,
                "sessions_held": pos["sessions"],
                "mae": pos["mae"],
                "mfe": pos["mfe"],
                "EV_net_signal": pos["EV_net"],
            })

        for date in all_dates:
            day = bars_by_date[date]

            # 1) Gérer les positions déjà ouvertes. Les entrées du jour ne sont jamais
            #    exposées artificiellement à la barre complète qui a précédé leur fill.
            for ticker in list(positions):
                if ticker not in day.index:
                    continue
                bar = day.loc[ticker]
                pos = positions[ticker]
                pos["sessions"] += 1
                pos["mae"] = min(pos["mae"], float(bar["low"]) / pos["entry_price"] - 1)
                pos["mfe"] = max(pos["mfe"], float(bar["high"]) / pos["entry_price"] - 1)
                stop_level = pos["entry_price"] * (1 - self.cfg.stop_pct)
                if float(bar["low"]) <= stop_level:
                    raw_exit = min(stop_level, float(bar["open"])) if float(bar["open"]) < stop_level else stop_level
                    close_position(ticker, date, bar, "STOP_-9%" if raw_exit == stop_level else "STOP_GAP_THROUGH", raw_exit)
                elif pos["sessions"] >= self.cfg.max_hold_sessions:
                    close_position(ticker, date, bar, "TIME_26W", float(bar["close"]))

            # 2) Entrées J+1, classement EV_net décroissant figé à la date du signal.
            candidates = sorted(scheduled.get(date, []), key=lambda r: (-float(r["EV_net"]), str(r["ticker"])))
            for sig in candidates:
                ticker = str(sig["ticker"])
                if ticker in positions:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "ALREADY_OPEN"})
                    continue
                if ticker not in day.index:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "NO_ENTRY_BAR"})
                    continue
                if len(positions) >= self.cfg.max_positions:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "MAX_POSITIONS"})
                    continue
                ym = (date.year, date.month)
                if entries_month.get(ym, 0) >= self.cfg.max_entries_month:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "MAX_ENTRIES_MONTH"})
                    continue
                if entries_year.get(date.year, 0) >= self.cfg.max_entries_year:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "MAX_ENTRIES_YEAR"})
                    continue
                open_px = float(day.loc[ticker, "open"])
                buy_px = open_px * (1 + self.cfg.slippage_rate)
                affordable_budget = min(self.cfg.max_position_eur, cash)
                unit_cash = buy_px * (1 + self.cfg.fee_rate)
                shares = floor(affordable_budget / unit_cash)
                if shares < 1:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "INSUFFICIENT_CASH"})
                    continue
                gross = shares * buy_px
                entry_fee = gross * self.cfg.fee_rate
                cash_out = gross + entry_fee
                if cash_out > cash + 1e-9:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "INSUFFICIENT_CASH"})
                    continue
                cash -= cash_out
                positions[ticker] = {
                    "signal_date": sig["date"], "entry_date": date, "shares": shares,
                    "entry_price": buy_px, "entry_fee": entry_fee, "cash_out": cash_out,
                    "EV_net": float(sig["EV_net"]), "sessions": 0, "mae": 0.0, "mfe": 0.0,
                }
                entries_month[ym] = entries_month.get(ym, 0) + 1
                entries_year[date.year] = entries_year.get(date.year, 0) + 1

            # 3) Valorisation quotidienne au close. Si un ticker n'a pas de barre ce jour,
            #    dernière clôture connue interdite implicitement: valeur d'entrée conservatrice.
            market_value = 0.0
            for ticker, pos in positions.items():
                px = float(day.loc[ticker, "close"]) if ticker in day.index else pos["entry_price"]
                market_value += pos["shares"] * px
            equity_rows.append({"date": date, "cash": cash, "market_value": market_value,
                                "equity": cash + market_value, "open_positions": len(positions)})

        # Clôture de fin de période au dernier close disponible, explicitement EOP.
        last_date = all_dates[-1]
        last_day = bars_by_date[last_date]
        for ticker in list(positions):
            if ticker in last_day.index:
                close_position(ticker, last_date, last_day.loc[ticker], "EOP", float(last_day.loc[ticker, "close"]))
            else:
                skipped.append({"signal_date": positions[ticker]["signal_date"], "ticker": ticker, "reason": "BLOCK_EOP_NO_BAR"})
        # Recalcul final de l'equity après liquidation EOP.
        equity_rows.append({"date": last_date, "cash": cash, "market_value": 0.0,
                            "equity": cash, "open_positions": len(positions)})

        ledger_df = pd.DataFrame(ledger)
        equity_df = pd.DataFrame(equity_rows).sort_values("date").reset_index(drop=True)
        skipped_df = pd.DataFrame(skipped)
        metrics = self._metrics(ledger_df, equity_df)
        quarterly = self._period_returns(equity_df, "QE")
        yearly = self._period_returns(equity_df, "YE")
        return {"ledger": ledger_df, "equity": equity_df, "skipped": skipped_df,
                "metrics": metrics, "quarterly": quarterly, "yearly": yearly}

    def _metrics(self, ledger: pd.DataFrame, equity: pd.DataFrame) -> dict:
        final_value = float(equity.iloc[-1]["equity"])
        net = final_value - self.cfg.initial_cash
        ret = final_value / self.cfg.initial_cash - 1
        eq = equity.drop_duplicates("date", keep="last").set_index("date")["equity"].astype(float)
        peak = eq.cummax(); dd = eq / peak - 1
        days = max(1, (eq.index.max() - eq.index.min()).days)
        cagr = (final_value / self.cfg.initial_cash) ** (365.25 / days) - 1 if final_value > 0 else -1.0
        if ledger.empty:
            wins = losses = pd.Series(dtype=float)
            pf = np.nan
            avg_win = avg_loss = rr = np.nan
            win_rate = 0.0
            fees = 0.0
            stops = 0
            mae = mfe = np.nan
        else:
            wins = ledger.loc[ledger["pnl_net"] > 0, "pnl_net"]
            losses = ledger.loc[ledger["pnl_net"] < 0, "pnl_net"]
            gross_profit = float(wins.sum()); gross_loss = float(-losses.sum())
            pf = gross_profit / gross_loss if gross_loss > 0 else (np.inf if gross_profit > 0 else np.nan)
            avg_win = float(wins.mean()) if len(wins) else np.nan
            avg_loss = float(losses.mean()) if len(losses) else np.nan
            rr = avg_win / abs(avg_loss) if np.isfinite(avg_win) and np.isfinite(avg_loss) and avg_loss != 0 else np.nan
            win_rate = float((ledger["pnl_net"] > 0).mean())
            fees = float(ledger["fees_total"].sum())
            stops = int(ledger["exit_reason"].str.startswith("STOP").sum())
            mae = float(ledger["mae"].mean()); mfe = float(ledger["mfe"].mean())
        invested = equity["market_value"].sum()
        gross_eq = (equity["cash"] + equity["market_value"]).replace(0, np.nan)
        utilization = float((equity["market_value"] / gross_eq).mean())
        return {
            "initial_capital": self.cfg.initial_cash, "final_value": final_value,
            "net_result_eur": net, "return_pct": ret, "cagr": cagr,
            "max_drawdown": float(dd.min()), "trades": int(len(ledger)),
            "wins": int((ledger["pnl_net"] > 0).sum()) if not ledger.empty else 0,
            "losses": int((ledger["pnl_net"] <= 0).sum()) if not ledger.empty else 0,
            "win_rate": win_rate, "profit_factor": float(pf) if pd.notna(pf) else np.nan,
            "avg_win_eur": avg_win, "avg_loss_eur": avg_loss, "rr_realized": rr,
            "stops": stops, "fees_eur": fees, "avg_mae": mae, "avg_mfe": mfe,
            "avg_capital_utilization": utilization,
        }

    @staticmethod
    def _period_returns(equity: pd.DataFrame, freq: str) -> pd.DataFrame:
        e = equity.drop_duplicates("date", keep="last").set_index("date")["equity"].astype(float)
        if e.empty:
            return pd.DataFrame(columns=["period", "start_equity", "end_equity", "return_pct"])
        rows = []
        for period, grp in e.groupby(e.index.to_period(freq)):
            start = float(grp.iloc[0]); end = float(grp.iloc[-1])
            rows.append({"period": str(period), "start_equity": start, "end_equity": end,
                         "return_pct": end / start - 1 if start else np.nan})
        return pd.DataFrame(rows)
