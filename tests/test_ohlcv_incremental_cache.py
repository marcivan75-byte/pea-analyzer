from __future__ import annotations

import numpy as np
import pandas as pd

from v182.sources.yfinance_bulk import _merge_history_frames, _overlap_rebase_detected


def test_incremental_merge_preserves_history_and_peer_values():
    columns = pd.MultiIndex.from_product([["AAA.PA", "BBB.PA"], ["Close"]])
    old = pd.DataFrame(
        [[10.0, 20.0], [11.0, 21.0]],
        index=pd.date_range("2026-08-11", periods=2),
        columns=columns,
    )
    fresh = pd.DataFrame(
        [[11.5, np.nan], [12.0, 22.0]],
        index=pd.date_range("2026-08-12", periods=2),
        columns=columns,
    )
    merged = _merge_history_frames(old, fresh)
    assert merged.loc[pd.Timestamp("2026-08-11"), ("AAA.PA", "Close")] == 10.0
    assert merged.loc[pd.Timestamp("2026-08-12"), ("AAA.PA", "Close")] == 11.5
    assert merged.loc[pd.Timestamp("2026-08-12"), ("BBB.PA", "Close")] == 21.0
    assert merged.loc[pd.Timestamp("2026-08-13"), ("BBB.PA", "Close")] == 22.0


def test_adjusted_history_revision_requires_full_batch_rebuild_but_latest_session_is_ignored():
    columns = pd.MultiIndex.from_product([["AAA.PA"], ["Open", "Close"]])
    idx = pd.date_range("2026-08-10", periods=4)
    old = pd.DataFrame(
        [[10.0, 10.2], [10.2, 10.4], [10.4, 10.6], [10.6, 10.8]],
        index=idx,
        columns=columns,
    )
    revised = old.copy()
    revised.loc[idx[1], ("AAA.PA", "Close")] = 5.2
    assert _overlap_rebase_detected(old, revised) is True

    current_only = old.copy()
    current_only.loc[idx[-1], ("AAA.PA", "Close")] = 11.1
    assert _overlap_rebase_detected(old, current_only) is False
