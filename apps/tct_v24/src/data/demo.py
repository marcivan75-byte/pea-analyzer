import pandas as pd
import numpy as np
from pathlib import Path

def generate_demo_signals(n: int = 120, path: str = "data/processed/latest_signals.parquet") -> pd.DataFrame:
    """
    Génère un jeu de signaux réalistes pour tester le pipeline sans données live.
    """
    np.random.seed(42)
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    secteurs = [
        "Industrials", "Financial Services", "Technology", "Healthcare",
        "Consumer Cyclical", "Basic Materials", "Energy", "Utilities",
        "Real Estate", "Communication Services", "Consumer Defensive"
    ]

    df = pd.DataFrame({
        "isin": [f"FR{str(i).zfill(10)}" for i in range(n)],
        "ticker": [f"TK{i:04d}.PA" for i in range(n)],
        "pea_eligible": True,
        "secteur": np.random.choice(secteurs, n),
        "close": np.round(np.random.uniform(5, 1200, n), 2),
        "market_cap_m": np.round(np.random.lognormal(6.5, 1.4, n), 1),
        "meta_proba": np.round(np.random.uniform(0.38, 0.91, n), 3),
        "days_to_earnings": np.random.choice([1, 2, 3, 4, 5, 7, 12, 20, 45], n, p=[0.08,0.10,0.12,0.12,0.12,0.15,0.12,0.10,0.09]),
        "eps_revision_3m": np.round(np.random.uniform(-10, 16, n), 2),
        "short_interest": np.round(np.random.uniform(0.5, 22, n), 2),
        "beat_rate": np.round(np.random.uniform(30, 85, n), 1),
        "atr_pct": np.round(np.random.uniform(0.012, 0.07, n), 4),
        "vol_ratio": np.round(np.random.uniform(0.7, 4.2, n), 2),
        "avg_dollar_volume_20d": np.random.lognormal(13.5, 1.3, n),
        "bandwidth": np.round(np.random.uniform(0.025, 0.14, n), 4),
        "score_earnings_proximity": np.random.randint(25, 96, n),
        "score_final": np.round(np.random.uniform(35, 78, n), 1),
        "setup": np.random.choice(["T1", "T2_CONFIRMATION", None], n, p=[0.25, 0.08, 0.67]),
        "bonus": 0,
    })

    # Cohérence bonus / setup
    df.loc[df["setup"] == "T1", "bonus"] = 15
    df.loc[df["setup"] == "T2_CONFIRMATION", "bonus"] = 30

    try:
        df.to_parquet(path, index=False)
    except Exception:
        path = path.replace('.parquet', '.csv') if path.endswith('.parquet') else path
        df.to_csv(path, index=False)
    print(f"Demo signals générés : {path} ({len(df)} lignes)")
    return df
