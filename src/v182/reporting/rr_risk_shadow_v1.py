"""Shadow R:R risk overlay. Does not size live orders."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.reporting.entry_exit_shadow_v1 import CI_PATHS, _num


ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = Path("config/RR_RISK_SHADOW_POLICY_V1.json")
OUT_CSV = Path("outputs/committee_master/RR_RISK_SHADOW_V1.csv")
OUT_MD = Path("outputs/mobile/RR_RISK_SHADOW_V1.md")
OUT_AUDIT = Path("outputs/audit/RR_RISK_SHADOW_V1.json")


def _policy(root: Path) -> dict:
    path = root / POLICY_PATH
    if path.exists() and path.stat().st_size:
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _read_ci(root: Path) -> pd.DataFrame:
    for relative in CI_PATHS:
        path = root / relative
        if path.exists() and path.stat().st_size:
            return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    return pd.DataFrame()


def _rr(reward_px: float | None, risk_px: float | None) -> float | None:
    if reward_px is None or risk_px is None or risk_px <= 0:
        return None
    return round(reward_px / risk_px, 2)


def _size(rr: float | None, spec: dict) -> float:
    if rr is None or rr < 2:
        return float(spec.get("rr_lt_2", 0.0))
    if rr < 3:
        return float(spec.get("rr_2_to_3", 0.5))
    return float(spec.get("rr_ge_3", 1.0))


def run(root: Path = ROOT) -> dict:
    policy = _policy(root)
    frame = _read_ci(root)
    if frame.empty:
        payload = {"status": "SKIPPED_NO_CI", "rows": 0, "decision_influence": 0.0}
    else:
        rows = []
        for _, row in frame.iterrows():
            price = _num(row.get("SIM_CURRENT_PRICE"))
            entry = _num(row.get("SIM_ENTRY_OPTIMAL"))
            target = _num(row.get("SIM_TARGET_CENTRAL"))
            invalid = _num(row.get("SIM_INVALIDATION"))
            rr_opt = _num(row.get("SIM_REWARD_RISK_AT_OPTIMAL_ENTRY"))
            if rr_opt is None and target is not None and entry is not None and invalid is not None:
                rr_opt = _rr(target - entry, entry - invalid)
            rr_now = None
            if target is not None and price is not None and invalid is not None:
                rr_now = _rr(target - price, price - invalid)
            above_opt = bool(price is not None and entry is not None and price > entry)
            invalid_hit = bool(price is not None and invalid is not None and price < invalid)
            entry_ok = bool(rr_opt is not None and rr_opt >= float(policy.get("entry_rr_min", 2)) and not above_opt)
            exit_rr = bool(
                rr_now is not None and rr_now < float(policy.get("exit_rr_from_current_min", 1))
            )
            if invalid_hit:
                action = "EXIT_INVALIDATION_SHADOW"
            elif exit_rr:
                action = "EXIT_RR_COMPRESSED_SHADOW"
            elif above_opt:
                action = "WAIT_PRICE_ABOVE_OPTIMAL_SHADOW"
            elif entry_ok:
                action = "RR_ENTRY_ELIGIBLE_SHADOW"
            else:
                action = "RR_REJECT_SHADOW"
            rec = dict(row)
            rec["RR_AT_OPTIMAL"] = rr_opt
            rec["RR_AT_CURRENT"] = rr_now
            rec["RR_PRICE_ABOVE_OPTIMAL"] = above_opt
            rec["RR_ENTRY_OK"] = entry_ok
            rec["RR_SOFT_SIZE"] = _size(rr_opt if not above_opt else rr_now, policy.get("size_soft") or {})
            rec["RR_RISK_ACTION"] = action
            rec["RR_DECISION_INFLUENCE"] = 0.0
            rows.append(rec)
        out = pd.DataFrame(rows)
        (root / OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(root / OUT_CSV, sep=";", index=False, encoding="utf-8-sig")
        lines = [
            "# Risque R:R SHADOW",
            "",
            "Entrée si R:R à l'optimal ≥ 2 et prix ≤ entrée optimale.",
            "Taille shadow: 0 / 0.5 / 1.0 selon R:R <2, 2–3, ≥3.",
            "Sortie: invalidation ou R:R spot < 1. Pas de take-profit fixe.",
            "",
            "| name | prix | entry | target | invalid | R:R opt | R:R now | size | action |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for _, row in out.head(15).iterrows():
            lines.append(
                "| {n} | {p} | {e} | {t} | {i} | {ro} | {rn} | {s} | {a} |".format(
                    n=row.get("name", ""),
                    p=row.get("SIM_CURRENT_PRICE", ""),
                    e=row.get("SIM_ENTRY_OPTIMAL", ""),
                    t=row.get("SIM_TARGET_CENTRAL", ""),
                    i=row.get("SIM_INVALIDATION", ""),
                    ro=row.get("RR_AT_OPTIMAL", ""),
                    rn=row.get("RR_AT_CURRENT", ""),
                    s=row.get("RR_SOFT_SIZE", ""),
                    a=row.get("RR_RISK_ACTION", ""),
                )
            )
        (root / OUT_MD).parent.mkdir(parents=True, exist_ok=True)
        (root / OUT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload = {
            "status": "SUCCESS",
            "rows": int(len(out)),
            "eligible": int(out["RR_ENTRY_OK"].sum()) if "RR_ENTRY_OK" in out else 0,
            "wait_above_optimal": int(out["RR_PRICE_ABOVE_OPTIMAL"].sum()) if "RR_PRICE_ABOVE_OPTIMAL" in out else 0,
            "decision_influence": 0.0,
            "real_orders_enabled": False,
        }
    (root / OUT_AUDIT).parent.mkdir(parents=True, exist_ok=True)
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["policy_version"] = policy.get("version")
    (root / OUT_AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
