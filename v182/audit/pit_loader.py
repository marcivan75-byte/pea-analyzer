"""
v182/audit/pit_loader.py
V22.5 AUDIT 5/5 - PIT strict T-1 22h fail-closed, mtime check, no future leak
Conforme MASTER_DATA_CONTRACT_V21_9
"""
import pandas as pd
from pathlib import Path
from datetime import datetime, time

class PITLoader:
    def __init__(self, root: Path = Path(".")):
        self.root=root
        self.audit_dir=root/"outputs"/"audit"
    def load_as_of(self, as_of_date: pd.Timestamp, asset_type="ACTION") -> pd.DataFrame:
        as_of=pd.Timestamp(as_of_date)
        cutoff=pd.Timestamp.combine(as_of.date(), time(22,0)) - pd.Timedelta(days=1)  # T-1 22h
        # Cherche fichiers avec mtime <= cutoff
        candidates=list(self.audit_dir.glob(f"{asset_type.lower()}*.parquet")) + list(self.audit_dir.glob(f"{asset_type.lower()}*.csv"))
        valid=[]
        for p in candidates:
            mtime=pd.Timestamp.fromtimestamp(p.stat().st_mtime)
            if mtime <= cutoff:
                valid.append((mtime,p))
        if not valid:
            # Fallback: si pas d'audit, essaie master PIT
            master=self.root/f"data/master/{asset_type.lower()}_master.parquet"
            if master.exists() and pd.Timestamp.fromtimestamp(master.stat().st_mtime) <= cutoff:
                return pd.read_parquet(master)
            raise FileNotFoundError(f"BLOCK_DATA PIT: aucun fichier {asset_type} avec mtime <= {cutoff} dans {self.audit_dir}")
        # Prend le plus récent <= cutoff
        valid.sort(key=lambda x: x[0], reverse=True)
        latest=valid[0][1]
        if latest.suffix=='.parquet':
            return pd.read_parquet(latest)
        return pd.read_csv(latest)
