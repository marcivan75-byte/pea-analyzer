"""TABPORT HEBDO AT META - simulateur de portefeuille 65 k€ gouverné.

Entrées: signaux PIT (date, ticker, EV_net[, tier]) et OHLC journaliers réels.
Sélection: EV_net décroissante à la date du signal; exécution à l'ouverture J+1 marché.
Aucune donnée synthétique et aucun retuning du holdout ne sont effectués ici.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor

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
        bad |= ((p["low"] > p["high"]) | (p["open"] < p["low"]) | (p["open"] > p["high"]) |
                (p["close"] < p["low"]) | (p["close"] > p["high"]))
        if bad.any():
            raise ValueError("BLOCK_DATA_TABPORT: invalid OHLC/date/ticker")
        if p.duplicated(["date", "ticker"]).any():
            raise ValueError("BLOCK_DATA_TABPORT: duplicate price bar")
        return p.sort_values(["date", "ticker"]).reset_index(drop=True)

    def run(self, signals: pd.DataFrame, prices: pd.DataFrame) -> dict:
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
        last_price_date = p.groupby("ticker")["date"].max().to_dict()
        scheduled: dict[pd.Timestamp, list[dict]] = {}
        skipped: list[dict] = []
        for _, row in s.iterrows():
            nxt = next((d for d in price_dates.get(row["ticker"], []) if d > row["date"]), None)
            if nxt is None:
                skipped.append({"signal_date": row["date"], "ticker": row["ticker"], "reason": "NO_J1_BAR"})
            else:
                scheduled.setdefault(nxt, []).append(row.to_dict())

        bars_by_date = {d: g.set_index("ticker") for d, g in p.groupby("date", sort=True)}
        all_dates = sorted(bars_by_date)
        cash = float(self.cfg.initial_cash)
        positions: dict[str, dict] = {}
        ledger: list[dict] = []
        equity_rows: list[dict] = []
        entries_month: dict[tuple[int, int], int] = {}
        entries_year: dict[int, int] = {}

        def close_position(ticker: str, date: pd.Timestamp, reason: str, raw_exit: float) -> None:
            nonlocal cash
            pos = positions.pop(ticker)
            sell_px = float(raw_exit) * (1 - self.cfg.slippage_rate)
            gross = sell_px * pos["shares"]
            sell_fee = gross * self.cfg.fee_rate
            cash += gross - sell_fee
            pnl_net = (gross - sell_fee) - pos["cash_out"]
            ledger.append({
                "ticker": ticker, "signal_date": pos["signal_date"], "entry_date": pos["entry_date"],
                "exit_date": date, "shares": pos["shares"], "entry_price": pos["entry_price"],
                "exit_price": sell_px, "entry_fee": pos["entry_fee"], "exit_fee": sell_fee,
                "fees_total": pos["entry_fee"] + sell_fee, "slippage_rate_side": self.cfg.slippage_rate,
                "cash_invested": pos["cash_out"], "pnl_net": pnl_net,
                "return_net": pnl_net / pos["cash_out"], "exit_reason": reason,
                "sessions_held": pos["sessions"], "mae": pos["mae"], "mfe": pos["mfe"],
                "EV_net_signal": pos["EV_net"],
            })

        def mark_bar(ticker: str, bar: pd.Series) -> tuple[float, float]:
            pos = positions[ticker]
            pos["last_close"] = float(bar["close"])
            pos["mae"] = min(pos["mae"], float(bar["low"]) / pos["entry_price"] - 1)
            pos["mfe"] = max(pos["mfe"], float(bar["high"]) / pos["entry_price"] - 1)
            return pos["entry_price"] * (1 - self.cfg.stop_pct), float(bar["open"])

        for date in all_dates:
            day = bars_by_date[date]

            for ticker in list(positions):
                if ticker not in day.index:
                    continue
                bar = day.loc[ticker]
                positions[ticker]["sessions"] += 1
                stop_level, open_px = mark_bar(ticker, bar)
                if float(bar["low"]) <= stop_level:
                    raw_exit = open_px if open_px < stop_level else stop_level
                    close_position(ticker, date, "STOP_GAP_THROUGH" if raw_exit < stop_level else "STOP_-9%", raw_exit)
                elif positions[ticker]["sessions"] >= self.cfg.max_hold_sessions:
                    close_position(ticker, date, "TIME_26W", float(bar["close"]))
                elif date == last_price_date[ticker]:
                    close_position(ticker, date, "EOP_DATA_END", float(bar["close"]))

            candidates = sorted(scheduled.get(date, []), key=lambda r: (-float(r["EV_net"]), str(r["ticker"])))
            for sig in candidates:
                ticker = str(sig["ticker"])
                if ticker in positions:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "ALREADY_OPEN"}); continue
                if ticker not in day.index:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "NO_ENTRY_BAR"}); continue
                if len(positions) >= self.cfg.max_positions:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "MAX_POSITIONS"}); continue
                ym = (date.year, date.month)
                if entries_month.get(ym, 0) >= self.cfg.max_entries_month:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "MAX_ENTRIES_MONTH"}); continue
                if entries_year.get(date.year, 0) >= self.cfg.max_entries_year:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "MAX_ENTRIES_YEAR"}); continue

                bar = day.loc[ticker]
                buy_px = float(bar["open"]) * (1 + self.cfg.slippage_rate)
                affordable = min(self.cfg.max_position_eur, cash)
                shares = floor(affordable / (buy_px * (1 + self.cfg.fee_rate)))
                if shares < 1:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "INSUFFICIENT_CASH"}); continue
                gross = shares * buy_px
                entry_fee = gross * self.cfg.fee_rate
                cash_out = gross + entry_fee
                if cash_out > cash + 1e-9:
                    skipped.append({"signal_date": sig["date"], "ticker": ticker, "reason": "INSUFFICIENT_CASH"}); continue
                cash -= cash_out
                positions[ticker] = {
                    "signal_date": sig["date"], "entry_date": date, "shares": shares,
                    "entry_price": buy_px, "entry_fee": entry_fee, "cash_out": cash_out,
                    "EV_net": float(sig["EV_net"]), "sessions": 1, "mae": 0.0, "mfe": 0.0,
                    "last_close": float(bar["close"]),
                }
                entries_month[ym] = entries_month.get(ym, 0) + 1
                entries_year[date.year] = entries_year.get(date.year, 0) + 1

                stop_level, open_px = mark_bar(ticker, bar)
                if float(bar["low"]) <= stop_level:
                    raw_exit = open_px if open_px < stop_level else stop_level
                    close_position(ticker, date, "STOP_GAP_THROUGH" if raw_exit < stop_level else "STOP_-9%", raw_exit)
                elif self.cfg.max_hold_sessions == 1:
                    close_position(ticker, date, "TIME_26W", float(bar["close"]))
                elif date == last_price_date[ticker]:
                    close_position(ticker, date, "EOP_DATA_END", float(bar["close"]))

            market_value = sum(pos["shares"] * pos["last_close"] for pos in positions.values())
            equity_rows.append({"date": date, "cash": cash, "market_value": market_value,
                                "equity": cash + market_value, "open_positions": len(positions)})

        if positions:
            raise ValueError(f"BLOCK_DATA_TABPORT: unclosed positions at EOP {sorted(positions)}")

        ledger_df = pd.DataFrame(ledger)
        equity_df = pd.DataFrame(equity_rows).sort_values("date").reset_index(drop=True)
        skipped_df = pd.DataFrame(skipped)
        return {
            "ledger": ledger_df, "equity": equity_df, "skipped": skipped_df,
            "metrics": self._metrics(ledger_df, equity_df),
            "quarterly": self._period_returns(equity_df, "Q", self.cfg.initial_cash),
            "yearly": self._period_returns(equity_df, "Y", self.cfg.initial_cash),
        }

    def _metrics(self, ledger: pd.DataFrame, equity: pd.DataFrame) -> dict:
        final_value = float(equity.iloc[-1]["equity"])
        net = final_value - self.cfg.initial_cash
        ret = final_value / self.cfg.initial_cash - 1
        eq = equity.drop_duplicates("date", keep="last").set_index("date")["equity"].astype(float)
        values = np.concatenate([[self.cfg.initial_cash], eq.to_numpy()])
        peak = np.maximum.accumulate(values)[1:]
        dd = eq.to_numpy() / peak - 1
        days = max(1, (eq.index.max() - eq.index.min()).days)
        cagr = (final_value / self.cfg.initial_cash) ** (365.25 / days) - 1 if final_value > 0 else -1.0
        if ledger.empty:
            pf = avg_win = avg_loss = rr = mae = mfe = np.nan
            win_rate = fees = 0.0; stops = 0
        else:
            wins = ledger.loc[ledger["pnl_net"] > 0, "pnl_net"]
            losses = ledger.loc[ledger["pnl_net"] < 0, "pnl_net"]
            gp, gl = float(wins.sum()), float(-losses.sum())
            pf = gp / gl if gl > 0 else (np.inf if gp > 0 else np.nan)
            avg_win = float(wins.mean()) if len(wins) else np.nan
            avg_loss = float(losses.mean()) if len(losses) else np.nan
            rr = avg_win / abs(avg_loss) if pd.notna(avg_win) and pd.notna(avg_loss) and avg_loss != 0 else np.nan
            win_rate = float((ledger["pnl_net"] > 0).mean())
            fees = float(ledger["fees_total"].sum())
            stops = int(ledger["exit_reason"].str.startswith("STOP").sum())
            mae, mfe = float(ledger["mae"].mean()), float(ledger["mfe"].mean())
        capital = (equity["cash"] + equity["market_value"]).replace(0, np.nan)
        utilization = float((equity["market_value"] / capital).mean())
        return {
            "initial_capital": self.cfg.initial_cash, "final_value": final_value,
            "net_result_eur": net, "return_pct": ret, "cagr": cagr,
            "max_drawdown": float(np.min(dd)), "trades": int(len(ledger)),
            "wins": int((ledger["pnl_net"] > 0).sum()) if not ledger.empty else 0,
            "losses": int((ledger["pnl_net"] <= 0).sum()) if not ledger.empty else 0,
            "win_rate": win_rate, "profit_factor": float(pf) if pd.notna(pf) else np.nan,
            "avg_win_eur": avg_win, "avg_loss_eur": avg_loss, "rr_realized": rr,
            "stops": stops, "fees_eur": fees, "avg_mae": mae, "avg_mfe": mfe,
            "avg_capital_utilization": utilization,
        }

    @staticmethod
    def _period_returns(equity: pd.DataFrame, freq: str, initial_capital: float) -> pd.DataFrame:
        e = equity.drop_duplicates("date", keep="last").set_index("date")["equity"].astype(float)
        if e.empty:
            return pd.DataFrame(columns=["period", "start_equity", "end_equity", "return_pct"])
        periods = e.index.tz_convert(None).to_period(freq)
        ends = e.groupby(periods).last()
        rows = []
        previous = float(initial_capital)
        for period, end in ends.items():
            end = float(end)
            rows.append({"period": str(period), "start_equity": previous, "end_equity": end,
                         "return_pct": end / previous - 1 if previous else np.nan})
            previous = end
        return pd.DataFrame(rows)
