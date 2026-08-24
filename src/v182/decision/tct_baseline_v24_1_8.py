from __future__ import annotations

import numpy as np
import pandas as pd

from v182.decision.tct_baseline_v24_1_7 import (
    BaselineAudit, WEIGHTS_V24_1_2, SETUP_COMPONENT, build_baseline_components,
)
from v182.decision.tct_v24_1_7 import universe_gate

ACTIVE_WEIGHT=sum(w for name,w in WEIGHTS_V24_1_2.items() if name!=SETUP_COMPONENT)
NORMALIZATION_POLICY="ACTIVE_AVAILABLE_PILLARS_RENORMALIZED_TO_100_SETUP_EXCLUDED"

# Explicit eligibility labels carried by the PEA actions master. Keep this list
# intentionally narrow: PEA_CANDIDATE / MEDIUM_INDEX_CONSTITUENT are not proof.
EXPLICIT_PEA_TYPES={"PEA_PME","PEA_PME+INDEX","PEA","PEA_CONFIRMED"}
EXPLICIT_PEA_CONFIDENCE={"HIGH_PEA_PME_LIST","HIGH_PEA_CONFIRMED","HIGH_PEA_PROOF"}


def _baseline_pea_pass(row: pd.Series) -> bool:
    """Accept the legacy gate or explicit PEA proof fields from the master.

    This repairs the label/parser mismatch that classified every PEA_PME row as
    PEA_PROOF_MISSING. Candidate-only labels remain fail-closed.
    """
    if universe_gate(row).passed:
        return True
    pea_type=str(row.get("pea_type") or "").strip().upper()
    confidence=str(row.get("pea_confidence") or "").strip().upper()
    return pea_type in EXPLICIT_PEA_TYPES or confidence in EXPLICIT_PEA_CONFIDENCE


def build_tct_baseline(actions: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame,BaselineAudit]:
    """V24.1.8 baseline: missing active pillars are reweighted to 100%.

    SETUP/T1/T2 remains intentionally excluded and has zero baseline influence.
    This is a new shadow scoring variant; historical V24.1.7 performance must not
    be attributed to it until a dedicated PIT backtest is completed.
    """
    if actions.empty:
        empty=actions.copy()
        for col in ("tct_baseline_score","tct_baseline_coverage","tct_baseline_rank","tct_baseline_top20","tct_baseline_status"):
            empty[col]=pd.Series(dtype=float if col not in {"tct_baseline_top20","tct_baseline_status"} else object)
        return empty,BaselineAudit(0,0,0,0,0,None,None)
    out=actions.copy(); components=build_baseline_components(out); weighted=pd.Series(0.0,index=out.index,dtype=float); observed_weight=pd.Series(0.0,index=out.index,dtype=float)
    for name,weight in WEIGHTS_V24_1_2.items():
        if name==SETUP_COMPONENT: continue
        values=pd.to_numeric(components[name],errors="coerce"); observed=values.notna(); weighted+=values.fillna(0.0)*float(weight); observed_weight+=observed.astype(float)*float(weight); out[f"tct_baseline_component_{name}"]=values; out[f"tct_baseline_component_{name}_observed"]=observed
    out["tct_baseline_component_setup"]=np.nan; out["tct_baseline_component_setup_observed"]=False
    out["tct_baseline_score"]=(weighted/observed_weight.replace(0,np.nan)).clip(0,100).round(4)
    out["tct_baseline_coverage"]=(observed_weight/ACTIVE_WEIGHT).clip(0,1).round(4)
    out["tct_baseline_effective_weight_sum_pct"]=np.where(observed_weight>0,100.0,0.0)
    pea_pass=pd.Series([_baseline_pea_pass(row) for _,row in out.iterrows()],index=out.index,dtype=bool); min_coverage=float(cfg.get("scope",{}).get("baseline_min_coverage",0.60)); coverage_pass=out["tct_baseline_coverage"]>=min_coverage; rankable=pea_pass&coverage_pass&out["tct_baseline_score"].notna(); rank=pd.Series(pd.NA,index=out.index,dtype="Int64")
    if rankable.any(): rank.loc[rankable]=out.loc[rankable,"tct_baseline_score"].rank(method="first",ascending=False).astype("Int64")
    out["tct_baseline_rank"]=rank; top_n=int(cfg.get("scope",{}).get("baseline_top_n",20)); out["tct_baseline_top20"]=rank.notna()&(rank<=top_n); status=pd.Series("EXCLUDED_PEA_GATE",index=out.index,dtype=object); status.loc[pea_pass&~coverage_pass]="BLOCK_BASELINE_COVERAGE"; status.loc[rankable]="BASELINE_RANKED"; status.loc[out["tct_baseline_top20"]]="BASELINE_TOP20"; out["tct_baseline_status"]=status; out["tct_baseline_missing_weight_policy"]=NORMALIZATION_POLICY; out["tct_baseline_setup_active"]=False; out["tct_baseline_t1_t2_influence"]=0.0; out["tct_baseline_scoring_version"]="V24.1.8_DYNAMIC_NORMALIZATION_SHADOW"
    audit=BaselineAudit(universe_rows=int(len(out)),pea_gate_pass_rows=int(pea_pass.sum()),coverage_pass_rows=int((pea_pass&coverage_pass).sum()),ranked_rows=int(rank.notna().sum()),top20_rows=int(out["tct_baseline_top20"].sum()),max_score=float(out["tct_baseline_score"].max()) if out["tct_baseline_score"].notna().any() else None,max_coverage=float(out["tct_baseline_coverage"].max()) if out["tct_baseline_coverage"].notna().any() else None)
    return out,audit
