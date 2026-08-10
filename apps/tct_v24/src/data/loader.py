import yfinance as yf
import pandas as pd
import numpy as np
import time
from typing import Dict, Optional, Callable, Any
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("data_loader")

class DataLoader:
    def __init__(self, finnhub_key: Optional[str] = None, cache_dir: str = "data/raw"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.finnhub_key = finnhub_key
        self._finnhub = None
        self._finnhub_min_interval = 1.05
        self._last_finnhub_call = 0.0

    @property
    def finnhub(self):
        if self._finnhub is None and self.finnhub_key:
            try:
                import finnhub
                self._finnhub = finnhub.Client(api_key=self.finnhub_key)
            except Exception as e:
                logger.warning(f"Finnhub non initialisé : {e}")
        return self._finnhub


    def _paced_finnhub_call(self, fn: Callable[..., Any], *args, **kwargs):
        """Execute one Finnhub request and enforce the free-plan pacing per call."""
        elapsed = time.monotonic() - self._last_finnhub_call
        if self._last_finnhub_call and elapsed < self._finnhub_min_interval:
            time.sleep(self._finnhub_min_interval - elapsed)
        try:
            return fn(*args, **kwargs)
        finally:
            self._last_finnhub_call = time.monotonic()
            # A post-call delay makes back-to-back calls safe even when the next
            # caller does not go through this helper immediately.
            time.sleep(self._finnhub_min_interval)

    def download_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        cache_file = self.cache_dir / f"{ticker}_{period}_{interval}.parquet"
        try:
            if cache_file.exists():
                df = pd.read_parquet(cache_file)
                age_hours = (pd.Timestamp.now(tz="UTC") - df.index.max().tz_localize("UTC") 
                             if df.index.max().tzinfo is None 
                             else pd.Timestamp.now(tz="UTC") - df.index.max()).total_seconds() / 3600
                if age_hours < 12:
                    return df

            df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
            if df is None or df.empty or len(df) < 50:
                logger.warning(f"Données insuffisantes pour {ticker}")
                return None

            # yfinance peut retourner un MultiIndex même pour un seul ticker.
            # Il faut l'aplatir AVANT de convertir les labels en chaînes.
            if isinstance(df.columns, pd.MultiIndex):
                level0 = [str(c).lower() for c in df.columns.get_level_values(0)]
                if len(set(level0)) == len(level0):
                    df.columns = level0
                else:
                    # Cas multi-tickers inattendu : conserve le premier ticker seulement.
                    first_ticker = df.columns.get_level_values(-1)[0]
                    try:
                        df = df.xs(first_ticker, axis=1, level=-1, drop_level=True)
                    except Exception:
                        df.columns = df.columns.get_level_values(0)
                    df.columns = [str(c).lower() for c in df.columns]
            else:
                df.columns = [str(c).lower() for c in df.columns]

            df.to_parquet(cache_file)
            return df
        except Exception as e:
            logger.error(f"Erreur download {ticker}: {e}")
            return None

    def get_earnings_info(self, ticker: str) -> Dict:
        default = {
            "days_to_earnings": np.nan,
            "eps_revision_3m": np.nan,
            "beat_rate": np.nan
        }
        if not self.finnhub:
            return default

        try:
            now_paris = pd.Timestamp.now(tz="Europe/Paris")
            today = now_paris.strftime("%Y-%m-%d")
            end = (now_paris + pd.Timedelta(days=90)).strftime("%Y-%m-%d")
            cal = self._paced_finnhub_call(self.finnhub.earnings_calendar, _from=today, to=end, symbol=ticker)

            days = np.nan
            if cal and cal.get("earningsCalendar"):
                next_date = pd.to_datetime(cal["earningsCalendar"][0]["date"])
                if next_date.tzinfo is None:
                    next_date = next_date.tz_localize("Europe/Paris")
                days = (next_date.normalize() - now_paris.normalize()).days
                if days < 0:
                    days = np.nan

            estimates = self._paced_finnhub_call(self.finnhub.company_earnings, ticker, limit=4)
            eps_rev = np.nan
            beat_rate = np.nan
            if estimates:
                surprises = []
                for e in estimates:
                    act = e.get("actual")
                    est = e.get("estimate")
                    if act is not None and est is not None and abs(est) > 1e-6:
                        surprises.append((act - est) / abs(est) * 100)
                if surprises:
                    eps_rev = float(np.mean(surprises[-3:])) if len(surprises) >= 3 else float(np.mean(surprises))
                    beat_rate = sum(1 for s in surprises if s > 0) / len(surprises) * 100

            return {
                "days_to_earnings": days,
                "eps_revision_3m": eps_rev,
                "beat_rate": beat_rate
            }
        except Exception as e:
            logger.warning(f"Erreur Finnhub {ticker}: {e}")
            return default
