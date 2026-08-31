import json
from pathlib import Path
import pandas as pd
import pytest

from v182.audit.pit_loader import PITLoader


def _write_csv_with_sidecar(path: Path, available_at: str):
    pd.DataFrame({'ticker':['ABC'], 'close':[100]}).to_csv(path, index=False)
    Path(str(path) + '.meta.json').write_text(
        json.dumps({'available_at':available_at}), encoding='utf-8'
    )


def test_pit_sidecar_provenance_selected(tmp_path: Path):
    audit = tmp_path / 'outputs' / 'audit'
    audit.mkdir(parents=True)
    f = audit / 'action_snapshot.csv'
    _write_csv_with_sidecar(f, '2026-08-29T21:00:00+02:00')
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
    _write_csv_with_sidecar(f, '2026-08-31T08:00:00+02:00')
    with pytest.raises(FileNotFoundError, match='BLOCK_DATA PIT'):
        PITLoader(tmp_path, strict_provenance=True).load_as_of(pd.Timestamp('2026-08-31'), 'ACTION')


def test_validate_explicit_provenance_accepts_snapshot_before_cutoff(tmp_path: Path):
    f = tmp_path / 'features.csv'
    _write_csv_with_sidecar(f, '2026-08-30T21:59:59+02:00')
    meta = PITLoader(tmp_path, strict_provenance=True).validate_explicit_provenance(
        f, pd.Timestamp('2026-08-31T09:00:00+02:00')
    )
    assert meta['source'] == 'sidecar'
    assert meta['snapshot_time'].startswith('2026-08-30T21:59:59')
    assert meta['cutoff'].startswith('2026-08-30T22:00:00')


def test_validate_explicit_provenance_rejects_snapshot_after_cutoff(tmp_path: Path):
    f = tmp_path / 'features.csv'
    _write_csv_with_sidecar(f, '2026-08-30T22:00:01+02:00')
    with pytest.raises(ValueError, match='after cutoff'):
        PITLoader(tmp_path, strict_provenance=True).validate_explicit_provenance(
            f, pd.Timestamp('2026-08-31T09:00:00+02:00')
        )


def test_validate_explicit_provenance_rejects_missing_sidecar(tmp_path: Path):
    f = tmp_path / 'features.csv'
    pd.DataFrame({'ticker':['ABC'], 'close':[100]}).to_csv(f, index=False)
    with pytest.raises(ValueError, match='explicit provenance required'):
        PITLoader(tmp_path, strict_provenance=True).validate_explicit_provenance(
            f, pd.Timestamp('2026-08-31')
        )


def test_cutoff_normalizes_foreign_timezone_to_paris_calendar_date(tmp_path: Path):
    loader=PITLoader(tmp_path)
    # 2026-08-31 23:30 UTC = 2026-09-01 01:30 Europe/Paris.
    cutoff=loader.cutoff_for(pd.Timestamp('2026-08-31T23:30:00+00:00'))
    assert cutoff.isoformat().startswith('2026-08-31T22:00:00+02:00')
