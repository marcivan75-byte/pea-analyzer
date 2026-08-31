"""
v182/audit/pit_loader.py
HEBDO AT META - loader PIT T-1 22h Europe/Paris avec provenance explicite si disponible.

Une métadonnée sidecar `<fichier>.meta.json` peut fournir `available_at` ou `snapshot_at`.
En mode strict_provenance=True, l'absence de cette métadonnée bloque le chargement.
"""
import json
import pandas as pd
from pathlib import Path
from datetime import time
from zoneinfo import ZoneInfo


class PITLoader:
    def __init__(self, root: Path = Path('.'), strict_provenance: bool = False):
        self.root = Path(root)
        self.audit_dir = self.root / 'outputs' / 'audit'
        self.strict_provenance = strict_provenance
        self.paris_tz = ZoneInfo('Europe/Paris')

    def cutoff_for(self, as_of_date: pd.Timestamp) -> pd.Timestamp:
        as_of = pd.Timestamp(as_of_date)
        if as_of.tzinfo is None:
            as_of = as_of.tz_localize(self.paris_tz)
        else:
            as_of = as_of.tz_convert(self.paris_tz)
        local_date = as_of.date()
        return pd.Timestamp.combine(local_date, time(22, 0)).tz_localize(self.paris_tz) - pd.Timedelta(days=1)

    # Compatibilité interne historique.
    def _cutoff(self, as_of_date: pd.Timestamp) -> pd.Timestamp:
        return self.cutoff_for(as_of_date)

    def _provenance_time(self, path: Path):
        sidecar = Path(str(path) + '.meta.json')
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding='utf-8'))
                raw = meta.get('available_at') or meta.get('snapshot_at')
                if raw:
                    ts = pd.Timestamp(raw)
                    if ts.tzinfo is None:
                        ts = ts.tz_localize(self.paris_tz)
                    else:
                        ts = ts.tz_convert(self.paris_tz)
                    return ts, 'sidecar'
            except (json.JSONDecodeError, OSError, ValueError, TypeError):
                if self.strict_provenance:
                    raise ValueError(f'BLOCK_DATA PIT: invalid provenance sidecar {sidecar}')
        if self.strict_provenance:
            raise ValueError(f'BLOCK_DATA PIT: explicit provenance required for {path}')
        mtime = pd.Timestamp(path.stat().st_mtime, unit='s', tz='UTC').tz_convert(self.paris_tz)
        return mtime, 'filesystem_mtime'

    def validate_explicit_provenance(self, path: Path, as_of_date: pd.Timestamp):
        """Valide un fichier externe contre le cutoff PIT en exigeant un sidecar explicite."""
        strict_loader = PITLoader(root=self.root, strict_provenance=True)
        ts, source = strict_loader._provenance_time(Path(path))
        cutoff = strict_loader.cutoff_for(as_of_date)
        if ts > cutoff:
            raise ValueError(
                f'BLOCK_DATA PIT: snapshot {ts.isoformat()} after cutoff {cutoff.isoformat()}'
            )
        return {'snapshot_time': ts.isoformat(), 'cutoff': cutoff.isoformat(), 'source': source}

    def _read(self, path: Path) -> pd.DataFrame:
        if path.suffix == '.parquet':
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def load_as_of(self, as_of_date: pd.Timestamp, asset_type='ACTION') -> pd.DataFrame:
        cutoff = self.cutoff_for(as_of_date)
        candidates = (
            list(self.audit_dir.glob(f'{asset_type.lower()}*.parquet'))
            + list(self.audit_dir.glob(f'{asset_type.lower()}*.csv'))
        )
        valid = []
        for p in candidates:
            ts, source = self._provenance_time(p)
            if ts <= cutoff:
                valid.append((ts, p, source))

        if not valid:
            master = self.root / f'data/master/{asset_type.lower()}_master.parquet'
            if master.exists():
                ts, source = self._provenance_time(master)
                if ts <= cutoff:
                    df = self._read(master)
                    df.attrs['pit_snapshot_time'] = ts.isoformat()
                    df.attrs['pit_provenance_source'] = source
                    df.attrs['pit_cutoff'] = cutoff.isoformat()
                    return df
            raise FileNotFoundError(
                f'BLOCK_DATA PIT: aucun fichier {asset_type} disponible avant {cutoff.isoformat()}'
            )

        valid.sort(key=lambda x: x[0], reverse=True)
        ts, latest, source = valid[0]
        df = self._read(latest)
        df.attrs['pit_snapshot_time'] = ts.isoformat()
        df.attrs['pit_provenance_source'] = source
        df.attrs['pit_cutoff'] = cutoff.isoformat()
        return df
