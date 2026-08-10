import numpy as np
import pandas as pd

def score_earnings_proximity(
    days: float,
    eps_rev: float,
    beat: float,
    short: float
) -> float:
    """
    Score Earnings Proximity 0-100 (version corrigée V24.1.1).
    Évite le double comptage et gère les NaN.
    """
    if pd.isna(days) or days < 0:
        return 30.0

    days = float(days)
    eps_rev = 0.0 if pd.isna(eps_rev) else float(eps_rev)
    beat = 50.0 if pd.isna(beat) else float(beat)
    short = 0.0 if pd.isna(short) else float(short)

    if days <= 5:
        if eps_rev >= 5 and short >= 15:
            score = 90.0
        elif eps_rev >= 3:
            score = 75.0
        elif eps_rev >= 0:
            score = 60.0
        else:
            score = 45.0
    elif days <= 10:
        score = 65.0 if eps_rev >= 5 else 50.0
    elif days <= 20:
        score = 55.0 if eps_rev >= 5 else 40.0
    else:
        score = 30.0

    # Bonus additionnels (sans double comptage excessif)
    if eps_rev >= 10:
        score += 15.0
    if beat >= 70:
        score += 10.0
    if short >= 15 and days <= 5:
        score += 10.0   # réduit par rapport à +20 original

    # Malus veille de publication
    if days <= 1:
        score -= 30.0

    return float(np.clip(score, 0.0, 100.0))
