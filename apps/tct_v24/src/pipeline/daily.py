from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.ml.gap_risk import GapRiskModel
from src.ml.reinforcement import RLAgent
from src.portfolio.position_sizing import compute_final_position_size
from src.utils.logger import setup_logger

logger = setup_logger("daily_pipeline")


def _save_table(df: pd.DataFrame, parquet_path: Path) -> Path:
    """Prefer parquet, but keep a CSV fallback instead of silently losing outputs."""
    try:
        df.to_parquet(parquet_path, index=False)
        return parquet_path
    except Exception as exc:
        csv_path = parquet_path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        logger.warning(f"Parquet indisponible ({exc}) → fallback CSV {csv_path}")
        return csv_path


def _enforce_max_positions(final: pd.DataFrame, max_positions: int) -> pd.DataFrame:
    if max_positions <= 0 or "decision" not in final.columns:
        return final
    take_idx = final.index[final["decision"].eq("TAKE")]
    if len(take_idx) <= max_positions:
        return final

    rank_cols = [c for c in ("score_final", "meta_proba") if c in final.columns]
    ranked = final.loc[take_idx].copy()
    if rank_cols:
        for c in rank_cols:
            ranked[c] = pd.to_numeric(ranked[c], errors="coerce").fillna(-np.inf)
        ranked = ranked.sort_values(rank_cols, ascending=False)
    keep = set(ranked.head(max_positions).index)
    drop = [idx for idx in take_idx if idx not in keep]
    final.loc[drop, "decision"] = "IGNORE"
    final.loc[drop, "position_pct"] = 0.0
    final.loc[drop, "shares"] = 0
    if "sizing_reason" not in final.columns:
        final["sizing_reason"] = "OK"
    final.loc[drop, "sizing_reason"] = final.loc[drop, "sizing_reason"].astype(str).map(
        lambda x: "MAX_POSITIONS_CAP" if x in ("", "OK", "nan") else f"{x}|MAX_POSITIONS_CAP"
    )
    return final


def run_daily_pipeline(
    signals: pd.DataFrame,
    capital: float = 100_000,
    config: Optional[dict] = None,
) -> pd.DataFrame:
    """Gap Risk → optional RL shadow sizing → position sizing → persistence."""
    today = pd.Timestamp.now(tz="Europe/Paris").strftime("%Y-%m-%d")
    cfg = config or {}
    output_dir = Path(cfg.get("paths", {}).get("output", "output/"))
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if signals is None or signals.empty:
            logger.error("Aucun signal reçu")
            return pd.DataFrame()

        signals = signals.copy()
        logger.info(f"Début pipeline – {len(signals)} signaux")

        # ----- Gap Risk -----
        try:
            model_dir = cfg.get("gap_risk", {}).get("model_dir", "models/gap_risk")
            gap_model = GapRiskModel(model_dir=model_dir)
            gap_preds = gap_model.predict(signals)
            # Affectation explicite : évite DataFrame.join qui échoue si Free Capture
            # fournit déjà des colonnes p_adverse / expected_adverse_gap.
            for col in gap_preds.columns:
                signals[col] = gap_preds[col].reindex(signals.index)
            logger.info("Gap Risk terminé")
        except Exception as e:
            logger.exception(f"Échec Gap Risk : {e}")
            signals["p_adverse"] = 0.50
            signals["expected_adverse_gap"] = -0.08
            signals["gap_model_source"] = "pipeline_fallback"

        # ----- Reinforcement Learning (shadow par défaut) -----
        rl_cfg = cfg.get("reinforcement", {})
        rl_enabled = bool(rl_cfg.get("enabled", True))
        rl_apply = bool(rl_cfg.get("apply_to_sizing", False))
        rl_agent = None
        rl_validated = False
        if rl_enabled:
            try:
                rl_agent = RLAgent(
                    model_dir=rl_cfg.get("model_dir", "models/rl"),
                    alpha=float(rl_cfg.get("alpha", 0.05)),
                    epsilon=float(rl_cfg.get("epsilon", 0.12)),
                    epsilon_min=float(rl_cfg.get("epsilon_min", 0.03)),
                    epsilon_decay=float(rl_cfg.get("epsilon_decay", 0.995)),
                )
                rl_validated = rl_agent.is_validated(
                    min_real_outcomes=int(rl_cfg.get("min_validated_samples", 100))
                )
                if rl_apply and not rl_validated:
                    logger.warning("RL non validé sur outcomes réels → sizing RL forcé en shadow")
                    rl_apply = False
                logger.info(f"RL agent loaded (validated={rl_validated}, apply_to_sizing={rl_apply})")
            except Exception as e:
                logger.warning(f"RL agent unavailable : {e}")

        # ----- Position Sizing -----
        decisions = []
        for idx, row in signals.iterrows():
            try:
                rl_mult = 1.0
                rl_info = {}
                if rl_agent is not None:
                    try:
                        rl_info = rl_agent.decide(row, explore=False)
                        suggested = float(rl_info.get("rl_mult", 1.0))
                        rl_info["rl_suggested_mult"] = suggested
                        rl_info["rl_validated"] = bool(rl_validated)
                        rl_mult = suggested if rl_apply else 1.0
                    except Exception as e:
                        logger.debug(f"RL decision ignorée {row.get('isin', idx)} : {e}")

                res = compute_final_position_size(
                    setup=row.to_dict(),
                    meta_proba=row.get("meta_proba", np.nan),
                    p_adverse=row.get("p_adverse", np.nan),
                    expected_adverse_gap=row.get("expected_adverse_gap", np.nan),
                    days_to_earnings=row.get("days_to_earnings", 99),
                    capital=capital,
                    rl_mult=rl_mult,
                    config=cfg,
                )
                res.update({
                    k: rl_info[k]
                    for k in ("rl_action", "rl_action_name", "rl_q_values", "rl_suggested_mult", "rl_validated")
                    if k in rl_info
                })
                decisions.append(res)
            except Exception as e:
                logger.error(f"Erreur sizing {row.get('isin', idx)} : {e}")
                decisions.append({
                    "decision": "IGNORE",
                    "position_pct": 0.0,
                    "shares": 0,
                    "meta_mult": 0.0,
                    "gap_mult": 0.0,
                    "liq_mult": 0.0,
                    "raw_mult": 0.0,
                    "time_stop": "NONE",
                    "rl_mult": 1.0,
                    "sizing_reason": "SIZING_EXCEPTION",
                })

        decisions_df = pd.DataFrame(decisions, index=signals.index)
        # Si des colonnes de sizing existent en amont, la décision locale doit les remplacer.
        overlap = [c for c in decisions_df.columns if c in signals.columns]
        signals = signals.drop(columns=overlap, errors="ignore")
        final = signals.join(decisions_df).reset_index(drop=True)

        max_positions = int(cfg.get("position_sizing", {}).get("max_positions", 12) or 0)
        final = _enforce_max_positions(final, max_positions=max_positions)

        # ----- Sauvegarde -----
        try:
            all_path = _save_table(final, output_dir / f"all_signals_{today}.parquet")
            taken = final[final["decision"] == "TAKE"].copy()
            _save_table(taken, output_dir / f"signals_taken_{today}.parquet")
            logger.info(f"Sauvegarde OK – {len(taken)} signaux TAKE → {all_path}")
        except Exception as e:
            logger.exception(f"Échec sauvegarde : {e}")

        return final

    except Exception as e:
        logger.critical(f"Échec global pipeline : {e}", exc_info=True)
        return pd.DataFrame()
