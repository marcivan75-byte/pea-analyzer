from __future__ import annotations

import json

from v182.decision import actions_v210_finnhub_backfill as base


_DISABLED_PATHS: set[str] = set()
_ORIGINAL_GET_JSON = base._get_json


def _guarded_get_json(session, path: str, params: dict, max_retries: int = 2, backoff_seconds: float = 2.0):
    """Disable repeatedly forbidden optional endpoints without changing analyst semantics.

    Recommendation/search errors are never swallowed because an entitlement failure must not
    be misclassified as NO_ANALYST_COVERAGE. Only optional premium enrichment endpoints can
    degrade to an empty payload after a confirmed 403.
    """
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
        text = f"{type(exc).__name__}: {exc}"
        if "403" in text and path in {"/stock/price-target", "/stock/metric"}:
            _DISABLED_PATHS.add(path)
            return {}
        raise


def main() -> None:
    base._get_json = _guarded_get_json
    base.main()
    print("V21_ACTIONS_FINNHUB_BACKFILL_V2", json.dumps({
        "disabled_optional_endpoints": sorted(_DISABLED_PATHS),
        "recommendation_entitlement_errors_are_not_treated_as_no_coverage": True,
    }))


if __name__ == "__main__":
    main()
