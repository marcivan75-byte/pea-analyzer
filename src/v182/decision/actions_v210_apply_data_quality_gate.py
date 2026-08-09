from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'data/reference/V21.0_ACTIONS_PEA_CONFIG.json'
COMMITTEE = ROOT / 'outputs/V21.0_ACTIONS_PEA_1429_COMMITTEE.csv'
AUDIT = ROOT / 'outputs/audit/V21.0_ACTIONS_COMMITTEE_AUDIT.json'
CERT = ROOT / 'outputs/audit/V21.0_ACTIONS_COVERAGE_CERTIFICATION.json'
SUMMARY = ROOT / 'outputs/V21.0_ACTIONS_COMMITTEE_SUMMARY.md'


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({'true', '1', 'yes', 'oui'})


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    cert = json.loads(CERT.read_text(encoding='utf-8'))
    df = pd.read_csv(COMMITTEE, sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    if len(df) != 1429 or df['isin'].astype(str).nunique() != 1429:
        raise RuntimeError('V21 data-quality gate requires canonical 1429 committee')

    ready = _truthy(df.get('coverage_decision_ready_v21', pd.Series(False, index=df.index)))
    downgrades: dict[str, int] = {}

    # MT/LT are explicitly fundamental/valuation/prospective horizons. A BUY/WATCH signal
    # is not allowed to survive solely because missing weights were renormalized.
    for hz in ['mt', 'lt']:
        decision = df[f'decision_{hz}'].astype(str)
        actionable = decision.isin({'BUY_CANDIDATE', 'WATCH'})
        blocked = actionable & ~ready
        downgrades[hz] = int(blocked.sum())
        df.loc[blocked, f'decision_{hz}'] = 'REVIEW'
        df.loc[blocked, f'decision_reason_{hz}'] = 'DATA_QUALITY_NOT_DECISION_READY'

        post_actionable = df[f'decision_{hz}'].astype(str).isin({'BUY_CANDIDATE', 'WATCH'})
        score = pd.to_numeric(df[f'score_{hz}'], errors='coerce').where(post_actionable)
        df[f'committee_rank_{hz}'] = score.rank(method='min', ascending=False).astype('Int64')
        limit = int(cfg['selection_limits'][hz.upper()])
        df[f'selection_{hz}'] = pd.to_numeric(df[f'committee_rank_{hz}'], errors='coerce').le(limit) & post_actionable

    df['data_quality_gate_v21'] = pd.Series('PASS', index=df.index, dtype='object')
    df.loc[~ready, 'data_quality_gate_v21'] = 'NOT_DECISION_READY_MT_LT'
    df['data_quality_gate_generated_at_utc'] = datetime.now(timezone.utc).isoformat()
    df.to_csv(COMMITTEE, sep=';', index=False, encoding='utf-8-sig')

    audit = json.loads(AUDIT.read_text(encoding='utf-8')) if AUDIT.exists() else {'passed': True}
    audit['data_quality_gate'] = {
        'applied': True,
        'process_validation_status': cert.get('process_validation_status'),
        'decision_ready_rows_pct': cert.get('metrics_pct', {}).get('decision_ready_rows_pct'),
        'downgraded_actionable_signals': downgrades,
        'rule': 'MT/LT BUY_CANDIDATE or WATCH requires coverage_decision_ready_v21=true; otherwise REVIEW',
        'no_score_change': True,
        'no_weight_change': True,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    audit['decisions_after_data_quality_gate'] = {
        hz: df[f'decision_{hz}'].astype(str).value_counts().to_dict()
        for hz in ['ct', 'mt', 'lt', 'short']
    }
    audit['selection_after_data_quality_gate'] = {
        hz: int(_truthy(df[f'selection_{hz}']).sum())
        for hz in ['ct', 'mt', 'lt', 'short']
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')

    with SUMMARY.open('a', encoding='utf-8') as fh:
        fh.write('\n\n## Data-quality decision gate\n')
        fh.write(f"- Process validation: **{cert.get('process_validation_status')}**\n")
        fh.write(f"- MT signals downgraded to REVIEW for insufficient decision data: {downgrades['mt']}\n")
        fh.write(f"- LT signals downgraded to REVIEW for insufficient decision data: {downgrades['lt']}\n")
        fh.write('- Scores and weights are unchanged; the gate only prevents strong decisions from insufficiently documented rows.\n')

    print('V21_ACTIONS_DATA_QUALITY_GATE_OK', {
        'process_status': cert.get('process_validation_status'),
        'downgrades': downgrades,
        'remaining_mt_actionable': int(df['decision_mt'].astype(str).isin({'BUY_CANDIDATE', 'WATCH'}).sum()),
        'remaining_lt_actionable': int(df['decision_lt'].astype(str).isin({'BUY_CANDIDATE', 'WATCH'}).sum()),
    })


if __name__ == '__main__':
    main()
