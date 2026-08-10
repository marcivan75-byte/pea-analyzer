from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

from v182.sources.eia_energy import fetch_energy_context
from v182.sources.eurostat_hicp_current import eurostat_hicp
from v182.sources.fred_macro import fetch_macro_context
from v182.sources.funnel_context import _ecb_deposit
from v182.sources.market_sentiment import collect_market_sentiment
from v182.sources.news_resilient import news_score

from .core import CaptureStore


ROOT = Path(__file__).resolve().parents[3]
FUNNEL_CONFIG = ROOT / "data/reference/V21.0_ACTIONS_FUNNEL_CONFIG.json"
EUROSTAT_COUNTRIES = ("FR", "DE", "IT", "ES", "NL", "BE")


def _safe(name: str, fn, store: CaptureStore) -> dict:
    try:
        value = fn()
        status = str(value.get("status") or "OK") if isinstance(value, dict) else "OK"
        store.add_health(name, status, attempted=1, succeeded=1 if status not in {"ERROR", "NO_DATA"} else 0)
        return value if isinstance(value, dict) else {"status": status, "value": value}
    except Exception as exc:
        store.add_health(name, "ERROR", attempted=1, failed=1, message=f"{type(exc).__name__}: {str(exc)[:500]}")
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def capture(store: CaptureStore) -> dict:
    cfg = json.loads(FUNNEL_CONFIG.read_text(encoding="utf-8"))
    fred_key = str(os.getenv("FRED_API_KEY") or "").strip()
    eia_key = str(os.getenv("EIA_API_KEY") or "").strip()

    out: dict[str, object] = {
        "version": "V21.1_COMPLEMENTARY_CONTEXT",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "free_only": True,
    }

    if fred_key:
        out["fred"] = _safe("FRED", lambda: fetch_macro_context(fred_key), store)
    else:
        out["fred"] = {"status": "SKIPPED_NO_KEY"}
        store.add_health("FRED", "SKIPPED_NO_KEY")

    out["ecb"] = _safe("ECB", lambda: _ecb_deposit(cfg), store)

    hicp: dict[str, dict] = {}
    for country in EUROSTAT_COUNTRIES:
        hicp[country] = _safe(f"EUROSTAT_{country}", lambda c=country: eurostat_hicp(c, cfg), store)
    out["eurostat_hicp"] = hicp

    if eia_key:
        out["eia"] = _safe("EIA", lambda: fetch_energy_context(eia_key), store)
    else:
        out["eia"] = {"status": "SKIPPED_NO_KEY"}
        store.add_health("EIA", "SKIPPED_NO_KEY")

    out["market_sentiment"] = _safe("CNN_FEAR_GREED_AAII", collect_market_sentiment, store)
    out["global_news"] = _safe(
        "GDELT_GOOGLE_NEWS",
        lambda: news_score(
            "(economy OR inflation OR interest rates OR recession OR growth OR central bank OR geopolitics)",
            cfg,
        ),
        store,
    )

    path = store.root / "V21.1_COMPLEMENTARY_CONTEXT.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return {
        "status": "OK",
        "output": str(path),
        "fred": out["fred"].get("status", "OK") if isinstance(out["fred"], dict) else "OK",
        "ecb": out["ecb"].get("status", "OK") if isinstance(out["ecb"], dict) else "OK",
        "eia": out["eia"].get("status", "OK") if isinstance(out["eia"], dict) else "OK",
        "news": out["global_news"].get("status", "OK") if isinstance(out["global_news"], dict) else "OK",
    }
