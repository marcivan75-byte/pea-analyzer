"""Build a governed PRE-2023 provider inventory from EODHD exchange lists.

This is deliberately NOT a certified historical registry. It merges the
provider's active and delisted common-stock lists without inventing listing
dates. Certification happens only after historical EOD bars establish observed
coverage and the registry validator accepts the resulting evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

BASE_URL = "https://eodhd.com/api/exchange-symbol-list"
REQUIRED_EXCHANGE_COLUMNS = ["eodhd_exchange", "mic", "country", "scope_evidence"]
OUTPUT_COLUMNS = [
    "provider",
    "eodhd_exchange",
    "mic",
    "country",
    "provider_code",
    "eodhd_symbol",
    "name",
    "instrument_type",
    "isin",
    "currency",
    "provider_status",
    "scope_evidence",
]


def load_exchange_scope(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        raise ValueError(f"BLOCK_PRE2023_EXCHANGE_SCOPE: missing/empty {p}")
    df = pd.read_csv(p, dtype=str).fillna("")
    missing = [c for c in REQUIRED_EXCHANGE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"BLOCK_PRE2023_EXCHANGE_SCOPE_SCHEMA: missing columns {missing}")
    df = df[REQUIRED_EXCHANGE_COLUMNS].copy()
    for c in REQUIRED_EXCHANGE_COLUMNS:
        df[c] = df[c].str.strip()
    if any((df[c] == "").any() for c in REQUIRED_EXCHANGE_COLUMNS):
        raise ValueError("BLOCK_PRE2023_EXCHANGE_SCOPE_QUALITY: blank mandatory field")
    if df["eodhd_exchange"].duplicated().any():
        raise ValueError("BLOCK_PRE2023_EXCHANGE_SCOPE_QUALITY: duplicate exchange")
    return df


def _pick(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_symbol_rows(
    rows: list[dict], *, exchange: str, mic: str, country: str,
    status: str, scope_evidence: str,
) -> pd.DataFrame:
    if status not in {"active", "delisted"}:
        raise ValueError(f"BLOCK_PRE2023_PROVIDER_STATUS: unsupported {status}")
    records: list[dict] = []
    for raw in rows:
        code = _pick(raw, "Code", "code")
        if not code:
            raise ValueError("BLOCK_PRE2023_PROVIDER_SCHEMA: blank Code")
        full_symbol = code if "." in code else f"{code}.{exchange}"
        records.append({
            "provider": "EODHD",
            "eodhd_exchange": exchange,
            "mic": mic,
            "country": country,
            "provider_code": code,
            "eodhd_symbol": full_symbol,
            "name": _pick(raw, "Name", "name"),
            "instrument_type": _pick(raw, "Type", "type"),
            "isin": _pick(raw, "Isin", "ISIN", "isin"),
            "currency": _pick(raw, "Currency", "currency"),
            "provider_status": status,
            "scope_evidence": scope_evidence,
        })
    return pd.DataFrame.from_records(records, columns=OUTPUT_COLUMNS)


def merge_active_delisted(active: pd.DataFrame, delisted: pd.DataFrame) -> pd.DataFrame:
    df = pd.concat([active, delisted], ignore_index=True)
    if df.empty:
        raise ValueError("BLOCK_PRE2023_PROVIDER_EMPTY: no active/delisted symbols")
    dup = df["eodhd_symbol"].duplicated(keep=False)
    if dup.any():
        symbols = sorted(df.loc[dup, "eodhd_symbol"].unique().tolist())
        raise ValueError(f"BLOCK_PRE2023_PROVIDER_COLLISION: symbols in multiple sets {symbols[:10]}")
    if not (df["provider_status"] == "delisted").any():
        raise ValueError("BLOCK_PRE2023_SURVIVORSHIP: provider inventory has no delisted symbols")
    return df.sort_values(["eodhd_exchange", "eodhd_symbol"]).reset_index(drop=True)


def fetch_exchange_symbols(exchange: str, token: str, *, delisted: bool) -> list[dict]:
    if not token:
        raise ValueError("BLOCK_PRE2023_SECRET: EODHD_API_TOKEN missing")
    # The PRE2023 stock-picking development universe is equities only. Request
    # common stocks explicitly so ETFs/funds/warrants cannot enter by accident.
    query = urlencode({
        "api_token": token,
        "fmt": "json",
        "delisted": int(delisted),
        "type": "common_stock",
    })
    url = f"{BASE_URL}/{exchange}?{query}"
    with urlopen(url, timeout=60) as response:  # nosec B310 - fixed HTTPS provider endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"BLOCK_PRE2023_PROVIDER_RESPONSE: {exchange} returned non-list payload")
    return payload


def build_inventory(scope_path: str | Path, output_path: str | Path, token: str) -> Path:
    scope = load_exchange_scope(scope_path)
    chunks: list[pd.DataFrame] = []
    for row in scope.to_dict("records"):
        ex = row["eodhd_exchange"]
        common = dict(exchange=ex, mic=row["mic"], country=row["country"], scope_evidence=row["scope_evidence"])
        active = normalize_symbol_rows(fetch_exchange_symbols(ex, token, delisted=False), status="active", **common)
        dead = normalize_symbol_rows(fetch_exchange_symbols(ex, token, delisted=True), status="delisted", **common)
        chunks.append(merge_active_delisted(active, dead))
    inventory = pd.concat(chunks, ignore_index=True)
    if inventory["eodhd_symbol"].duplicated().any():
        raise ValueError("BLOCK_PRE2023_PROVIDER_COLLISION: duplicate full symbols across exchanges")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    inventory.to_csv(out, index=False)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange-scope", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build_inventory(args.exchange_scope, args.output, os.environ.get("EODHD_API_TOKEN", ""))


if __name__ == "__main__":
    main()
