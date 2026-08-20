from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.features.etf_ct_lt_shadow_v21_14 import load_json, score_ct_lt_shadow

ROOT = Path(__file__).resolve().parents[3]


def run(root: Path = ROOT) -> dict:
    cfg = load_json(root / "config" / "ETF_CT_LT_SHADOW_V21_14.json")
    registry = load_json(root / cfg["source_registry"])
    input_path = root / cfg["input"]
    if not input_path.exists() or input_path.stat().st_size == 0:
        raise RuntimeError("ETF_CT_LT_ENRICHED_CURRENT_SNAPSHOT_REQUIRED")
    etfs = pd.read_csv(input_path, sep=";", encoding="utf-8-sig", low_memory=False)
    if "isin" not in etfs.columns or len(etfs) != 102 or etfs["isin"].nunique() != 102:
        unique = etfs["isin"].nunique() if "isin" in etfs.columns else 0
        raise RuntimeError(f"ETF_CT_LT_CANONICAL_UNIVERSE_REQUIRED:{len(etfs)}:{unique}")

    rows, summary = score_ct_lt_shadow(etfs, registry, cfg)
    summary.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_path": str(input_path.relative_to(root)),
            "canonical_universe_count": 102,
            "governance": cfg["governance"],
            "validation": cfg["validation"],
        }
    )

    history_depth_path = root / "outputs" / "audit" / "OHLCV_PRIMARY_HISTORY_DEPTH_SUMMARY.json"
    if history_depth_path.exists():
        try:
            history_depth = json.loads(history_depth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            history_depth = None
        if isinstance(history_depth, dict):
            summary["v21_13_history_depth_context"] = {
                "version": history_depth.get("version"),
                "as_of": history_depth.get("as_of"),
                "primary_calibration_eligible_count": history_depth.get("primary_calibration_eligible_count"),
                "stress_library_eligible_count": history_depth.get("stress_library_eligible_count"),
                "stress_calibration_weight": history_depth.get("stress_calibration_weight"),
            }

    out_dir = root / "outputs" / "etf_ct_lt_shadow"
    audit_dir = root / "outputs" / "audit"
    mobile_dir = root / "outputs" / "mobile"
    for directory in (out_dir, audit_dir, mobile_dir):
        directory.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "ETF_CT_LT_SHADOW.csv"
    json_path = audit_dir / "ETF_CT_LT_V21_14_SHADOW.json"
    mobile_path = mobile_dir / "ETF_CT_LT_SHADOW.md"
    rows.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")

    lines = [
        "# ETF PEA CT / LT — V21.14 SHADOW",
        "",
        "- Influence décisionnelle : **0**",
        "- Ordres réels : **désactivés**",
        "- T1/T2 : **interdits**",
        "- Seuils 77/70 : **bandes de contexte legacy uniquement**",
        "- Attribution de performance CT/LT : **aucune avant PIT/OOS dédié**",
        "- Critère trop peu renseigné cross-sectionnellement : **exclu du score shadow**",
        "",
    ]
    for horizon in ("CT", "LT"):
        subset = rows[rows["horizon"].eq(horizon)]
        info = summary["horizons"][horizon]
        lines.extend(
            [
                f"## {horizon}",
                "",
                f"- Scorables : {info['scorable_rows']}/{info['universe_rows']}",
                f"- Couverture minimale pondérée : {info['minimum_weighted_coverage']:.0%}",
                f"- Minimum cross-section : {info['minimum_cross_section_observations']} ETF observés",
                f"- Critères cross-sectionnels utilisables : {len(info['rankable_cross_section_criteria'])}/{info['configured_criteria']}",
                "",
            ]
        )
        for _, row in subset.dropna(subset=["shadow_score"]).sort_values("shadow_rank").head(10).iterrows():
            lines.append(
                f"- {row['name']} — {float(row['shadow_score']):.1f} — {row['shadow_context']} — couverture {float(row['weighted_coverage']):.0%}"
            )
        lines.append("")
    mobile_path.write_text("\n".join(lines), encoding="utf-8")

    summary["outputs"] = {
        "rows_csv": str(csv_path.relative_to(root)),
        "audit_json": str(json_path.relative_to(root)),
        "mobile_md": str(mobile_path.relative_to(root)),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
