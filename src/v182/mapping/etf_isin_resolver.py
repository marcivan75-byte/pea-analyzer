from __future__ import annotations
from pathlib import Path
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# Yahoo Finance ne propose pas de recherche fiable par ISIN. OpenFIGI v3 accepte
# l'ISIN en entrée (idType=ID_ISIN) et renvoie les identités par place de cotation.
# Le nombre de jobs par requête dépend de l'authentification ; on reste donc à
# cinq sans clé et au maximum à cent avec clé, sans contourner les limites source.
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# Correspondance code place OpenFIGI/Bloomberg -> suffixe ticker Yahoo Finance.
# Cette table couvre les places européennes les plus fréquentes pour des ETF
# PEA. A compléter si des ISIN restent non résolus (voir outputs/gaps).
EXCHANGE_TO_YAHOO_SUFFIX = {
    "PA": "PA", "AS": "AS", "BR": "BR", "LS": "LS", "MI": "MI",
    "GR": "DE", "GY": "DE", "SW": "SW", "VI": "VI", "IR": "IR",
    "HE": "HE", "CO": "CO", "ST": "ST", "OS": "OL", "LN": "L",
}
EXCHANGE_PREFERENCE = ["PA", "AS", "BR", "MI", "GR", "GY", "LS", "IR", "SW", "LN", "VI", "HE", "CO", "ST", "OS"]


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def resolve_isins(
    isins: list[str],
    api_key: str | None = None,
    batch_size: int | None = None,
    delay_seconds: float | None = None,
) -> dict[str, list[dict]]:
    """Interroge OpenFIGI v3 par lots avec limites adaptées à l'authentification."""
    import requests

    key = api_key or os.environ.get("OPENFIGI_API_KEY")
    max_batch = 100 if key else 5
    effective_batch = max_batch if batch_size is None else max(1, min(int(batch_size), max_batch))
    effective_delay = float(delay_seconds if delay_seconds is not None else (1.0 if key else 3.0))
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    results: dict[str, list[dict]] = {}
    clean_isins = sorted({i.strip() for i in isins if i and i.strip()})
    for batch in _batches(clean_isins, effective_batch):
        payload = [{"idType": "ID_ISIN", "idValue": isin} for isin in batch]
        try:
            resp = requests.post(OPENFIGI_URL, headers=headers, data=json.dumps(payload), timeout=20)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.warning("OpenFIGI batch failed for %s ISINs: %s: %s", len(batch), type(exc).__name__, str(exc)[:180])
            for isin in batch:
                results[isin] = []
            time.sleep(effective_delay)
            continue
        for isin, entry in zip(batch, body):
            results[isin] = entry.get("data", []) if isinstance(entry, dict) else []
        time.sleep(effective_delay)
    return results


def pick_best_match(matches: list[dict]) -> dict | None:
    by_exchange = {m.get("exchCode"): m for m in matches if m.get("exchCode") in EXCHANGE_TO_YAHOO_SUFFIX}
    for exch in EXCHANGE_PREFERENCE:
        if exch in by_exchange:
            return by_exchange[exch]
    return None


def build_etf_ticker_map(
    etf_master_path: str | Path,
    output_map_path: str | Path,
    gaps_path: str | Path,
    api_key: str | None = None,
) -> dict:
    import pandas as pd

    etf_df = pd.read_csv(etf_master_path, sep=";", encoding="utf-8-sig", dtype=str)
    output_map_path = Path(output_map_path)
    existing = (
        pd.read_csv(output_map_path, sep=";", encoding="utf-8-sig", dtype=str)
        if output_map_path.exists()
        else pd.DataFrame(columns=["isin", "yahoo_ticker"])
    )
    already_mapped = set(existing["isin"].dropna())
    to_resolve = [isin for isin in etf_df["isin"] if isin not in already_mapped]
    key = api_key or os.environ.get("OPENFIGI_API_KEY")
    resolved = resolve_isins(to_resolve, api_key=key)
    new_rows = []
    gaps = []
    for isin in to_resolve:
        matches = resolved.get(isin, [])
        best = pick_best_match(matches)
        if best is None:
            gaps.append({"isin": isin, "reason": "NO_OPENFIGI_MATCH_ON_KNOWN_EXCHANGE", "raw_matches": len(matches)})
            continue
        suffix = EXCHANGE_TO_YAHOO_SUFFIX[best["exchCode"]]
        new_rows.append({"isin": isin, "yahoo_ticker": f"{best['ticker']}.{suffix}"})
    updated = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True).drop_duplicates("isin")
    updated.to_csv(output_map_path, sep=";", index=False, encoding="utf-8-sig")
    if gaps:
        Path(gaps_path).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(gaps).to_csv(gaps_path, sep=";", index=False, encoding="utf-8-sig")
    return {"requested": len(to_resolve), "resolved": len(new_rows), "gaps": len(gaps)}


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[3]
    summary = build_etf_ticker_map(
        etf_master_path=root / "inputs" / "V18.2_PEA_ETF_MASTER.csv",
        output_map_path=root / "config" / "V18.2_ETF_TICKER_MAP.csv",
        gaps_path=root / "outputs" / "gaps" / "V18.2_ETF_TICKER_OPENFIGI_GAPS.csv",
    )
    print(f"OpenFIGI — {summary['resolved']}/{summary['requested']} ISIN résolus, {summary['gaps']} restent en gap (voir outputs/gaps).")
    sys.exit(0)
