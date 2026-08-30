"""
v182/scoring/ic_lasso_selector.py
Sélection IC + Lasso gouvernée avec contrat explicite train/inference.
Politique: fail-closed, pas de forward fill, pas d'estimation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LassoCV
from sklearn.preprocessing import StandardScaler


def compute_information_coefficient(
    df_features: pd.DataFrame,
    forward_returns: pd.Series,
) -> pd.DataFrame:
    """IC de Spearman entre chaque feature et le forward return vrai."""
    if df_features.empty:
        return pd.DataFrame(columns=["feature", "IC", "p_value", "n"])

    ics: list[dict[str, object]] = []
    for col in df_features.columns:
        x = pd.to_numeric(df_features[col], errors="coerce")
        aligned = pd.concat([x.rename("feature"), forward_returns.rename("forward_ret")], axis=1)
        mask = aligned["feature"].notna() & aligned["forward_ret"].notna()
        n = int(mask.sum())
        if n < 30:
            ic, pval = np.nan, np.nan
        else:
            ic, pval = spearmanr(aligned.loc[mask, "feature"], aligned.loc[mask, "forward_ret"])
        ics.append({"feature": col, "IC": ic, "p_value": pval, "n": n})

    return pd.DataFrame(ics).sort_values("IC", key=lambda s: s.abs(), ascending=False)


def lasso_select_features(
    X: pd.DataFrame,
    y: pd.Series,
    cv: int = 5,
    min_abs_coef: float = 1e-4,
):
    """Entraîne StandardScaler + LassoCV et conserve le contrat de scaling."""
    if X.empty or X.shape[1] == 0:
        raise ValueError("Aucune feature fournie au Lasso")
    if cv < 2:
        raise ValueError("cv doit être >= 2")
    if min_abs_coef < 0:
        raise ValueError("min_abs_coef doit être >= 0")

    numeric = X.apply(pd.to_numeric, errors="coerce")
    data = pd.concat([numeric, pd.to_numeric(y, errors="coerce").rename("forward_ret")], axis=1).dropna()
    if len(data) < 100:
        raise ValueError(f"Pas assez de données pour Lasso: {len(data)} rows")
    if len(data) < cv:
        raise ValueError(f"Pas assez de données pour cv={cv}: {len(data)} rows")

    X_clean = data[X.columns]
    y_clean = data["forward_ret"]
    features_names = X_clean.columns.tolist()

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X_clean)
    lasso = LassoCV(cv=cv, max_iter=20000, alphas=100, random_state=42).fit(Xs, y_clean)

    selected: list[dict[str, object]] = []
    for feature, coef, mean_, scale_ in zip(
        features_names,
        lasso.coef_,
        scaler.mean_,
        scaler.scale_,
        strict=True,
    ):
        if abs(coef) > min_abs_coef:
            if not np.isfinite(scale_) or scale_ <= 0:
                raise ValueError(f"Scale Lasso invalide pour {feature}")
            coef_raw = coef / scale_
            selected.append(
                {
                    "feature": feature,
                    "coef_lasso_standardized": float(coef),
                    "coef_raw": float(coef_raw),
                    "mean": float(mean_),
                    "scale": float(scale_),
                }
            )

    columns = ["feature", "coef_lasso_standardized", "coef_raw", "mean", "scale"]
    df_selected = pd.DataFrame(selected, columns=columns)
    if not df_selected.empty:
        df_selected = df_selected.sort_values(
            "coef_lasso_standardized",
            key=lambda s: s.abs(),
            ascending=False,
        )

    return df_selected, float(lasso.alpha_), lasso


def build_governed_weights(df_selected: pd.DataFrame) -> dict[str, dict[str, float | str]]:
    """Construit les poids et fige mean/scale d'entraînement pour l'inférence.

    Le scoring futur doit utiliser exactement ``(x-training_mean)/training_scale``.
    Une renormalisation cross-sectionnelle de la semaine courante est interdite car
    elle modifierait implicitement le modèle appris.
    """
    if df_selected.empty:
        return {}

    required = {"feature", "coef_lasso_standardized", "mean", "scale"}
    missing = required.difference(df_selected.columns)
    if missing:
        raise ValueError(f"Colonnes manquantes pour gouvernance: {sorted(missing)}")

    abs_coefs = pd.to_numeric(df_selected["coef_lasso_standardized"], errors="coerce").abs()
    denom = float(abs_coefs.sum())
    if not np.isfinite(denom) or denom <= 0:
        raise ValueError("Somme des coefficients Lasso invalide ou nulle")

    weights = abs_coefs / denom
    governed: dict[str, dict[str, float | str]] = {}
    for feat, weight, row in zip(
        df_selected["feature"],
        weights,
        df_selected.to_dict("records"),
        strict=True,
    ):
        coef = float(row["coef_lasso_standardized"])
        mean_ = float(row["mean"])
        scale_ = float(row["scale"])
        if not np.isfinite(mean_) or not np.isfinite(scale_) or scale_ <= 0:
            raise ValueError(f"Contrat de scaling invalide pour {feat}")
        governed[str(feat)] = {
            "weight": float(weight),
            "coef": coef,
            "direction": "SHORT" if coef < 0 else "LONG",
            "training_mean": mean_,
            "training_scale": scale_,
        }

    return governed


if __name__ == "__main__":
    print("IC Lasso selector chargé - scaling train/inference verrouillé")
