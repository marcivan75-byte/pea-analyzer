from __future__ import annotations

from pathlib import Path
import re
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "data" / "reference" / "ETF_PEA_V12_266_9_PILIERS.csv"
MASTER = ROOT / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _family(name: object) -> tuple[str, str] | tuple[None, None]:
    s = _norm(name)
    rules = [
        (("cac 40",), "Amundi CAC 40 PEA UCITS ETF"),
        (("dax",), "Amundi DAX PEA UCITS ETF"),
        (("euro stoxx 50", "eurostoxx 50"), "Amundi Euro Stoxx 50 PEA UCITS ETF"),
        (("emerging markets", "msci em "), "Amundi MSCI Emerging Markets PEA UCITS ETF"),
        (("europe small",), "Amundi MSCI Europe Small Cap PEA UCITS ETF"),
        (("india",), "Amundi MSCI India PEA UCITS ETF"),
        (("japan", "topix"), "Amundi MSCI Japan Topix PEA UCITS ETF"),
        (("world momentum",), "Amundi MSCI World Momentum PEA UCITS ETF"),
        (("world small",), "Amundi MSCI World Small Cap PEA UCITS ETF"),
        (("msci world", "world swap"), "Amundi MSCI World PEA UCITS ETF"),
        (("nasdaq",), "Amundi Nasdaq 100 PEA UCITS ETF"),
        (("s p 500", "sp 500", "s p500"), "Amundi S&P 500 PEA UCITS ETF"),
        (("europe quality",), "HSBC MSCI Europe Quality PEA UCITS ETF"),
        (("stoxx europe 600", "europe 600", "stoxx 600"), "Amundi Stoxx 600 PEA UCITS ETF"),
    ]
    for needles, family in rules:
        if any(n in s for n in needles):
            # Sector/thematic variants are only a family proxy, never exact.
            proxy = any(x in s for x in ["banks", "bank", "tech", "health", "industr", "telecom", "utilit", "insurance", "travel", "media", "auto", "chemical", "food", "retail"])
            return family, "family_proxy" if proxy else "family"
    return None, None


def apply_external_etf_enrichment(root: Path | None = None) -> dict:
    from v182.io.frames import load_master, save_master

    root = root or ROOT
    reference = root / "data" / "reference" / "ETF_PEA_V12_266_9_PILIERS.csv"
    master_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not reference.exists() or not master_path.exists():
        return {"rows": 0, "matched": 0, "reason": "missing_input"}

    ref = pd.read_csv(reference, sep=";", encoding="utf-8-sig", dtype=str).set_index("mapped_family")
    etf = load_master(master_path).astype(object)

    mapped = []
    levels = []
    for name in etf.get("name", pd.Series([""] * len(etf))):
        family, level = _family(name)
        mapped.append(family or "")
        levels.append(level or "")
    etf["external_9p_family"] = mapped
    etf["external_9p_match_level"] = levels
    etf["external_9p_source"] = ""

    source_to_target = {
        "median_aum_m_eur": "external_9p_aum_m_eur",
        "median_ter_pct": "external_9p_ter_pct",
        "median_spread_pct": "external_9p_spread_pct",
        "median_adv_m_eur_day": "external_9p_adv_m_eur_day",
        "median_srri": "external_9p_srri",
        "median_td_1y_pct": "external_9p_td_1y_pct",
        "median_td_3y_pct": "external_9p_td_3y_pct",
        "median_tracking_error_pct": "external_9p_tracking_error_pct",
        "median_max_dd_5y_pct": "external_9p_max_dd_5y_pct",
        "median_sharpe_5y": "external_9p_sharpe_5y",
        "median_score_v9": "external_9p_score_v9",
        "median_liquidity_score": "external_9p_liquidity_score",
        "median_esg_score": "external_9p_esg_score",
        "median_replication_score": "external_9p_replication_score",
        "median_availability_3": "external_9p_availability_3",
    }
    for target in source_to_target.values():
        etf[target] = ""

    matched = 0
    for idx, family in enumerate(mapped):
        if not family or family not in ref.index:
            continue
        matched += 1
        etf.at[etf.index[idx], "external_9p_source"] = "ETF_PEA_V12_266_9_PILIERS"
        row = ref.loc[family]
        for source, target in source_to_target.items():
            etf.at[etf.index[idx], target] = row.get(source, "")

    save_master(etf, master_path)
    return {"rows": len(etf), "matched": matched, "unmatched": len(etf) - matched}


def main() -> None:
    print("ETF_EXTERNAL_9P_ENRICHMENT", apply_external_etf_enrichment())


if __name__ == "__main__":
    main()
