import json
from pathlib import Path
import pandas as pd
import pytest

from v182.audit.pit_loader import PITLoader


def test_pit_sidecar_provenance_selected(tmp_path: Path):
    audit = tmp_path / 'outputs' / 'audit'
    audit.mkdir(parents=True)
    f = audit / 'action_snapshot.csv'
    pd.DataFrame({'ticker':['ABC'], 'close':[100]}).to_csv(f, index=False)
    Path(str(f) + '.meta.json').write_text(
        json.dumps({'available_at':'2026-08-29T21:00:00+02:00'}), encoding='utf-8'
    )
    df = PITLoader(tmp_path, strict_provenance=True).load_as_of(pd.Timestamp('2026-08-31'), 'ACTION')
    assert df.iloc[0]['ticker'] == 'ABC'
    assert df.attrs['pit_provenance_source'] == 'sidecar'
    assert '2026-08-29' in df.attrs['pit_snapshot_time']


def test_pit_strict_blocks_mtime_only(tmp_path: Path):
    audit = tmp_path / 'outputs' / 'audit'
    audit.mkdir(parents=True)
    f = audit / 'action_snapshot.csv'
    pd.DataFrame({'ticker':['ABC'], 'close':[100]}).to_csv(f, index=False)
    with pytest.raises(ValueError, match='explicit provenance required'):
        PITLoader(tmp_path, strict_provenance=True).load_as_of(pd.Timestamp('2026-08-31'), 'ACTION')


def test_pit_future_sidecar_not_accepted(tmp_path: Path):
    audit = tmp_path / 'outputs' / 'audit'
    audit.mkdir(parents=True)
    f = audit / 'action_snapshot.csv'
    pd.DataFrame({'ticker':['ABC'], 'close':[100]}).to_csv(f, index=False)
    Path(str(f) + '.meta.json').write_text(
        json.dumps({'available_at':'2026-08-31T08:00:00+02:00'}), encoding='utf-8'
    )
    with pytest.raises(FileNotFoundError, match='BLOCK_DATA PIT'):
        PITLoader(tmp_path, strict_provenance=True).load_as_of(pd.Timestamp('2026-08-31'), 'ACTION')
