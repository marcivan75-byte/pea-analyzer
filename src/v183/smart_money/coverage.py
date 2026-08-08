from __future__ import annotations

import re
import unicodedata
import pandas as pd


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


def provider_adapter(provider: object, name: object = "") -> str:
    key = f"{_norm(provider)}_{_norm(name)}"
    if "AMUNDI" in key or "LYXOR" in key:
        return "AMUNDI_LYXOR_NORMALIZED"
    if "BNP" in key or "PARIBAS" in key or "EASY" in key:
        return "BNP_PARIBAS_EASY_NORMALIZED"
    if "ISHARES" in key or "BLACKROCK" in key:
        return "BLACKROCK_ISHARES_NORMALIZED"
    if "XTRACKERS" in key or "DWS" in key:
        return "DWS_XTRACKERS_NORMALIZED"
    if "AXA" in key:
        return "AXA_IM_NORMALIZED"
    if "HSBC" in key:
        return "HSBC_NORMALIZED"
    if "VANGUARD" in key:
        return "VANGUARD_NORMALIZED"
    return "GENERIC_NORMALIZED_IMPORT"


def build_etf_registry(etf_master: pd.DataFrame, flow_history: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Map every ETF to a supported normalized ingestion path.

    Registry coverage and *live flow readiness* are intentionally separate.
    The generic normalized adapter guarantees that an ETF has an integration
    route; it does not pretend that AUM/NAV history already exists.
    """
    rows = etf_master.copy()
    if "isin" not in rows.columns:
        raise ValueError("ETF master must contain isin")
    if "provider" not in rows.columns:
        rows["provider"] = ""
    if "name" not in rows.columns:
        rows["name"] = ""
    rows = rows[["isin", "name", "provider"]].drop_duplicates("isin").copy()
    rows["adapter"] = [provider_adapter(p, n) for p, n in zip(rows["provider"], rows["name"])]
    rows["registry_supported"] = True

    ready_isins: set[str] = set()
    observation_counts: dict[str, int] = {}
    if flow_history is not None and not flow_history.empty and "isin" in flow_history.columns:
        counts = flow_history.groupby("isin").size()
        observation_counts = {str(k): int(v) for k, v in counts.items()}
        ready_isins = set(str(k) for k, v in counts.items() if int(v) >= 20)
    rows["flow_observations"] = rows["isin"].astype(str).map(observation_counts).fillna(0).astype(int)
    rows["flow_ready_20d"] = rows["isin"].astype(str).isin(ready_isins)

    total = len(rows)
    registered = int(rows["registry_supported"].sum())
    ready = int(rows["flow_ready_20d"].sum())
    provider_counts = (
        rows.assign(provider=rows["provider"].fillna("").replace("", "UNKNOWN"))
        .groupby(["provider", "adapter"], dropna=False)
        .size()
        .reset_index(name="count")
        .to_dict("records")
    )
    metrics = {
        "etf_total": total,
        "registry_supported": registered,
        "registry_coverage_pct": 100.0 if total == 0 else round(registered / total * 100, 2),
        "flow_ready_20d": ready,
        "flow_ready_20d_pct": 100.0 if total == 0 else round(ready / total * 100, 2),
        "providers": provider_counts,
        "coverage_semantics": {
            "registry": "ETF has a supported normalized ingestion route",
            "flow_ready_20d": "ETF has at least 20 persisted AUM+NAV observations",
        },
    }
    return rows, metrics
