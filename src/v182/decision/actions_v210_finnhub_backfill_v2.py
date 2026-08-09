from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pandas as pd

from v182.decision import actions_v210_finnhub_backfill as base


_DISABLED_PATHS: set[str] = set()
_ORIGINAL_GET_JSON = base._get_json


def _coverage(df: pd.DataFrame, field: str) -> float:
    if field not in df.columns:
        return 0.0
    return round(float(df[field].notna().mean() * 100.0), 2)


def _write_entitlement_skip(reason: str) -> None:
    df = pd.read_csv(base.TARGET, sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    analyst_status = df.get('analyst_coverage_status_v21', pd.Series(index=df.index, dtype=object)).astype('string').fillna('')
    checked = df.get('consensus_score_100_v21', pd.Series(index=df.index, dtype=object)).notna() | analyst_status.str.startswith('NO_ANALYST_COVERAGE_CONFIRMED')
    audit = {
        'passed': True,
        'status': 'SKIPPED_RECOMMENDATION_ENTITLEMENT',
        'reason': reason,
        'rows': len(df),
        'attempted': 0,
        'resolved': 0,
        'recommendation_observed': 0,
        'target_observed': 0,
        'metric_observed': 0,
        'filled_cells': 0,
        'analyst_process_coverage_pct': round(float(checked.mean() * 100.0), 2),
        'coverage_after_pct': {
            'consensus_score_100_v21': _coverage(df, 'consensus_score_100_v21'),
            'target_mean_v21': _coverage(df, 'target_mean_v21'),
            'consensus_delta_4w': _coverage(df, 'consensus_delta_4w'),
        },
        'recommendation_entitlement_errors_are_not_treated_as_no_coverage': True,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    base.AUDIT.parent.mkdir(parents=True, exist_ok=True)
    base.AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    print('V21_ACTIONS_FINNHUB_BACKFILL_V2_SKIPPED', json.dumps(audit, ensure_ascii=False))


def _preflight_recommendations(token: str) -> tuple[bool, str]:
    import requests
    session = requests.Session()
    try:
        # AIR.PA is used only as an entitlement probe; no returned value is written to the reference.
        _ORIGINAL_GET_JSON(session, '/stock/recommendation', {'symbol': 'AIR.PA', 'token': token}, max_retries=0)
        return True, 'OK'
    except Exception as exc:
        text = f'{type(exc).__name__}: {exc}'
        if '403' in text or '401' in text:
            return False, text[:220]
        # Network/transient errors do not prove missing entitlement; let the normal guarded run handle them.
        return True, text[:220]


def _guarded_get_json(session, path: str, params: dict, max_retries: int = 2, backoff_seconds: float = 2.0):
    """Disable repeatedly forbidden optional endpoints without corrupting analyst semantics."""
    if path in _DISABLED_PATHS:
        return {}
    try:
        return _ORIGINAL_GET_JSON(
            session,
            path,
            params,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
        )
    except Exception as exc:
        text = f'{type(exc).__name__}: {exc}'
        if ('403' in text or '401' in text) and path in {'/stock/price-target', '/stock/metric'}:
            _DISABLED_PATHS.add(path)
            return {}
        raise


def main() -> None:
    token = str(os.getenv('FINNHUB_API_KEY') or '').strip()
    if not token:
        base.main()
        return

    allowed, reason = _preflight_recommendations(token)
    if not allowed:
        _write_entitlement_skip(reason)
        return

    base._get_json = _guarded_get_json
    base.main()
    print('V21_ACTIONS_FINNHUB_BACKFILL_V2', json.dumps({
        'disabled_optional_endpoints': sorted(_DISABLED_PATHS),
        'recommendation_entitlement_errors_are_not_treated_as_no_coverage': True,
    }))


if __name__ == '__main__':
    main()
