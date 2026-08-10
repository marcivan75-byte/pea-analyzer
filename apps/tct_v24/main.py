#!/usr/bin/env python3
"""TCT – point d'entrée principal (application V24.1.4)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.data.amf_shorts import enrich_with_amf_shorts
from src.data.demo import generate_demo_signals
from src.data.euronext_shorts import enrich_with_euronext_shorts
from src.ml.adaptive_weights import AdaptiveWeightsEngine
from src.ml.meta_labeling import MetaLabelingModel, apply_meta_labeling
from src.gates.universe_gate import apply_universe_gate
from src.ml.reinforcement import RLAgent
from src.pipeline.build_signals import build_signals, read_signal_snapshot
from src.pipeline.daily import run_daily_pipeline
from src.pipeline.v21_daily import run_v21_pipeline
from src.signals.committee import (
    build_committee,
    build_dashboard_secteurs,
    extract_top20_research,
    extract_top50_opportunite,
    extract_ultra_earnings_squeeze,
)
from src.signals.scoring import WEIGHTS_V24_1_2
from src.signals.squeeze_pressure import apply_squeeze_pressure
from src.utils.helpers import load_config
from src.utils.logger import setup_logger
from src.utils.telegram_alert import alert_error, alert_pipeline_summary
from src.version import VERSION_LABEL

logger = setup_logger("main")


def _read_signals(config: dict) -> pd.DataFrame:
    """Backward-compatible reader used by tests/manual diagnostics.

    The production path calls ``build_signals`` and therefore never falls back
    silently to synthetic data.
    """
    try:
        return read_signal_snapshot(config)
    except FileNotFoundError:
        allow_demo = bool(config.get("runtime", {}).get("allow_demo_fallback", False))
        if not allow_demo:
            raise
        processed_dir = Path(config.get("paths", {}).get("processed", "data/processed/"))
        parquet_path = processed_dir / "latest_signals.parquet"
        logger.warning("Aucun snapshot réel → MODE DEMO explicitement autorisé")
        df = generate_demo_signals(n=180, path=str(parquet_path))
        df["data_mode"] = "DEMO"
        return df


def _save_table(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception as exc:
        csv = path.with_suffix(".csv")
        df.to_csv(csv, index=False)
        logger.warning(f"Parquet indisponible ({exc}) → fallback CSV {csv}")
        return csv


def _extract_real_outcomes(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Return only genuinely realized labels; never self-label current signals."""
    if df is None or df.empty:
        return None
    for col in ("realized_outcome", "outcome_real"):
        if col in df.columns:
            out = df.copy()
            out["outcome"] = pd.to_numeric(out[col], errors="coerce")
            out = out[out["outcome"].notna()]
            return out if not out.empty else None
    if "outcome" in df.columns and "outcome_source" in df.columns:
        mask = df["outcome_source"].astype(str).str.lower().isin({"real", "realized", "pnl_realized"})
        out = df.loc[mask].copy()
        out["outcome"] = pd.to_numeric(out["outcome"], errors="coerce")
        out = out[out["outcome"].notna()]
        return out if not out.empty else None
    return None


def main() -> None:
    logger.info("=" * 60)
    logger.info(f"TCT {VERSION_LABEL} – Démarrage du pipeline")
    logger.info("=" * 60)

    try:
        config = load_config(str(ROOT / "config/settings.yaml"))
        capital = float(config.get("runtime", {}).get("capital", 100_000))
        signals = build_signals(config)

        # ----- Universe Gate en amont : contraint aussi le sizing/comité V24 -----
        signals = apply_universe_gate(signals)
        n_reject = int((signals["universe_status"] == "REJECT").sum())
        n_quarantine = int((signals["universe_status"] == "QUARANTINE").sum())
        logger.info(f"Universe Gate amont – REJECT={n_reject} | QUARANTINE={n_quarantine}")

        # ----- Short Interest Euronext / AMF -----
        shorts_cfg = config.get("short_interest", {})
        skip_short_refresh = str(os.getenv("TCT_SKIP_SHORT_REFRESH", "")).strip().lower() in {"1", "true", "yes", "on"}
        if bool(shorts_cfg.get("enabled", True)) and not skip_short_refresh:
            try:
                signals = enrich_with_euronext_shorts(
                    signals,
                    force_refresh=bool(shorts_cfg.get("force_refresh", False)),
                )
                n_nca = int((signals.get("short_source") == "EURONEXT_NCA").sum()) if "short_source" in signals.columns else 0
                logger.info(f"Euronext/AMF shorts enrichis – {n_nca} titres NCA")
            except Exception as e:
                logger.warning(f"Euronext shorts ignorés, fallback AMF : {e}")
                try:
                    signals = enrich_with_amf_shorts(signals, force_refresh=False)
                except Exception as e2:
                    logger.warning(f"AMF fallback échoué : {e2}")

        # ----- Squeeze Pressure -----
        try:
            signals = apply_squeeze_pressure(signals)
            logger.info(
                f"Squeeze candidates : {int(signals['squeeze_candidate'].sum()) if 'squeeze_candidate' in signals.columns else 0}"
            )
        except Exception as e:
            logger.warning(f"Squeeze Pressure : {e}")

        # ----- Meta-labeling -----
        meta_cfg = config.get("meta_labeling", {})
        meta_model = MetaLabelingModel(
            model_dir=meta_cfg.get("model_dir", "models/meta_labeling"),
            fallback_proba=float(meta_cfg.get("fallback_proba", meta_cfg.get("min_proba", 0.55))),
        )
        signals = apply_meta_labeling(
            signals,
            model=meta_model,
            fallback_proba=float(meta_cfg.get("fallback_proba", meta_cfg.get("min_proba", 0.55))),
            preserve_upstream=bool(meta_cfg.get("preserve_upstream_if_model_missing", True)),
        )
        logger.info(f"Meta-labeling appliqué – sources={signals['meta_model_source'].value_counts().to_dict()}")

        # ----- Pipeline principal -----
        result = run_daily_pipeline(signals, capital=capital, config=config)
        if result.empty:
            raise RuntimeError("Pipeline principal vide")

        # ----- Poids actifs : historiques réels uniquement -----
        adaptive_cfg = config.get("adaptive_weights", {})
        adaptive_engine = None
        active_weights = dict(WEIGHTS_V24_1_2)
        if bool(adaptive_cfg.get("enabled", True)):
            adaptive_engine = AdaptiveWeightsEngine(
                learning_rate=float(adaptive_cfg.get("learning_rate", 0.08)),
                lookback_days=int(adaptive_cfg.get("lookback_days", 60)),
                min_samples=int(adaptive_cfg.get("min_samples", 30)),
                allow_proxy_learning=bool(adaptive_cfg.get("allow_proxy_learning", adaptive_cfg.get("proxy_learning_enabled", False))),
            )
            active_weights = adaptive_engine.get_weights()

        # ----- Comité complet -----
        result = build_committee(result, weights=active_weights)
        today = pd.Timestamp.now(tz="Europe/Paris").strftime("%Y%m%d")
        out = Path(config.get("paths", {}).get("output", "output/"))
        out.mkdir(parents=True, exist_ok=True)
        _save_table(result, out / f"committee_{today}.parquet")

        # ----- Pipeline V21.3 -----
        try:
            v21 = run_v21_pipeline(result, output_dir=str(out))
            logger.info(
                f"V21.3 decisions : {v21['decision_v21'].value_counts().to_dict() if not v21.empty and 'decision_v21' in v21.columns else {}}"
            )
        except Exception as e:
            logger.warning(f"V21.3 pipeline ignoré : {e}")

        dash = build_dashboard_secteurs(result)
        if not dash.empty:
            _save_table(dash, out / f"dashboard_secteurs_{today}.parquet")
            dash.to_csv(out / f"dashboard_secteurs_{today}.csv", index=False)

        research_top20 = extract_top20_research(result)
        if not research_top20.empty:
            _save_table(research_top20, out / f"top20_research_{today}.parquet")
            research_top20.to_csv(out / f"top20_research_{today}.csv", index=False)
            # Stable integration alias for downstream GitHub artifacts.
            research_top20.to_csv(out / f"{VERSION_LABEL}_TCT_TOP20_RESEARCH.csv", index=False)

        top50 = extract_top50_opportunite(result)
        if not top50.empty:
            _save_table(top50, out / f"top50_opportunite_{today}.parquet")
            top50.to_csv(out / f"top50_opportunite_{today}.csv", index=False)
        # Stable full/actionable aliases, even when actionable is empty.
        result.to_csv(out / f"{VERSION_LABEL}_TCT_FULL.csv", index=False)
        top50.to_csv(out / f"{VERSION_LABEL}_TCT_ACTIONABLE.csv", index=False)

        ultra = extract_ultra_earnings_squeeze(result)
        if not ultra.empty:
            _save_table(ultra, out / f"ultra_earnings_squeeze_{today}.parquet")
            ultra.to_csv(out / f"ultra_earnings_squeeze_{today}.csv", index=False)

        # ----- Audit stable pour intégration GitHub -----
        import json
        from datetime import datetime, timezone
        adapter_counts = (
            result["tct_adapter_source"].fillna("NATIVE_TCT_CONTRACT").astype(str).value_counts().to_dict()
            if "tct_adapter_source" in result.columns
            else {"NATIVE_TCT_CONTRACT": int(len(result))}
        )
        asset_counts = (
            result["tct_asset_class"].fillna("UNKNOWN").astype(str).value_counts().to_dict()
            if "tct_asset_class" in result.columns
            else {"UNKNOWN": int(len(result))}
        )
        expected_actions = int(config.get("universe", {}).get("n_actions", 0) or 0)
        expected_etf = int(config.get("universe", {}).get("n_etf", 0) or 0)
        full_target = expected_actions + expected_etf
        scope_status = (
            "FULL_CANONICAL_1931" if full_target and len(result) == full_target
            else "TRANSITIONAL_SCOPE"
        )
        audit_payload = {
            "passed": True,
            "version": VERSION_LABEL,
            "execution_mode": config.get("application", {}).get("mode", "RESEARCH_ONLY_SHADOW"),
            "rows": int(len(result)),
            "unique_isin": int(result["isin"].astype(str).nunique()) if "isin" in result.columns else 0,
            "input_adapter": next(iter(adapter_counts)) if len(adapter_counts) == 1 else "MIXED_CANONICAL_REPO_INPUTS",
            "input_adapters": adapter_counts,
            "asset_class_counts": asset_counts,
            "expected_actions": expected_actions,
            "expected_etf": expected_etf,
            "expected_total": full_target,
            "scope_status": scope_status,
            "universe_pass": int((result.get("universe_status") == "PASS").sum()) if "universe_status" in result.columns else 0,
            "universe_quarantine": int((result.get("universe_status") == "QUARANTINE").sum()) if "universe_status" in result.columns else 0,
            "universe_reject": int((result.get("universe_status") == "REJECT").sum()) if "universe_status" in result.columns else 0,
            "take": int((result.get("decision") == "TAKE").sum()) if "decision" in result.columns else 0,
            "actionable_top50": int(len(top50)),
            "research_top20": int(len(research_top20)),
            "t1": int((result.get("setup") == "T1").sum()) if "setup" in result.columns else 0,
            "t2": int((result.get("setup") == "T2_CONFIRMATION").sum()) if "setup" in result.columns else 0,
            "meta_model_ready": bool(meta_model.is_ready),
            "meta_sources": result["meta_model_source"].value_counts(dropna=False).to_dict() if "meta_model_source" in result.columns else {},
            "gap_sources": result["gap_model_source"].value_counts(dropna=False).to_dict() if "gap_model_source" in result.columns else {},
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        (out / f"{VERSION_LABEL}_TCT_AUDIT.json").write_text(
            json.dumps(audit_payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

        # ----- Learning: seulement outcomes réalisés -----
        realized = _extract_real_outcomes(result)
        if realized is not None:
            if adaptive_engine is not None:
                adaptive_engine.record_outcomes(realized, label_col="outcome")
                new_w = adaptive_engine.update_from_history(use_proxy_if_needed=False)
                logger.info(f"Poids adaptatifs mis à jour sur outcomes réels : { {k: round(v,3) for k,v in new_w.items()} }")

            rl_cfg = config.get("reinforcement", {})
            if bool(rl_cfg.get("enabled", True)) and bool(rl_cfg.get("learning_enabled", True)):
                try:
                    rl = RLAgent(
                        model_dir=rl_cfg.get("model_dir", "models/rl"),
                        alpha=float(rl_cfg.get("alpha", 0.05)),
                        epsilon=float(rl_cfg.get("epsilon", 0.12)),
                        epsilon_min=float(rl_cfg.get("epsilon_min", 0.03)),
                        epsilon_decay=float(rl_cfg.get("epsilon_decay", 0.995)),
                    )
                    n_rl = rl.learn_from_dataframe(realized, outcome_col="outcome")
                    logger.info(f"RL update sur outcomes réels : {n_rl} expériences")
                except Exception as e:
                    logger.warning(f"RL update ignorée : {e}")
        else:
            logger.info("Aucun outcome réalisé → aucun auto-apprentissage (anti-boucle de proxy)")

        n_take = int((result["decision"] == "TAKE").sum()) if "decision" in result.columns else 0
        n_t2 = int((result.get("setup") == "T2_CONFIRMATION").sum()) if "setup" in result.columns else 0
        top_isins = top50["isin"].head(5).tolist() if not top50.empty and "isin" in top50.columns else []
        logger.info(f"Pipeline terminé – TAKE={n_take} | T2={n_t2} | Ultra={len(ultra)}")
        alert_pipeline_summary(len(result), n_take, n_t2, len(ultra), top_isins)
        logger.info("=" * 60)

    except Exception as e:
        logger.critical(f"Échec global : {e}", exc_info=True)
        alert_error("main", str(e))
        raise


if __name__ == "__main__":
    main()
