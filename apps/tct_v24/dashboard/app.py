from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.version import VERSION_LABEL

st.set_page_config(page_title=f"TCT {VERSION_LABEL} Monitor", layout="wide", page_icon="📈")
st.title(f"TCT {VERSION_LABEL} – Monitoring Dashboard")
st.caption("Système quantitatif Très Court Terme PEA – Research Only")

output_dir = Path("output")
files = sorted(
    list(output_dir.glob("all_signals_*.parquet")) + list(output_dir.glob("all_signals_*.csv")),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)

if not files:
    st.warning("Aucun fichier de signaux trouvé dans /output. Lancez d'abord `python main.py`.")
    st.stop()

latest = files[0]
try:
    df = pd.read_parquet(latest) if latest.suffix == ".parquet" else pd.read_csv(latest)
except Exception as exc:
    st.error(f"Impossible de lire {latest.name}: {exc}")
    st.stop()

st.subheader(f"Dernière exécution : `{latest.stem}`")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Signaux totaux", len(df))
n_take = len(df[df["decision"] == "TAKE"]) if "decision" in df.columns else 0
col2.metric("Signaux TAKE", n_take)
n_t2 = len(df[df["setup"].isin(["T2_CONFIRMATION", "T2_ULTRA"])]) if "setup" in df.columns else 0
col3.metric("T2 / Ultra", n_t2)
avg_meta = f"{pd.to_numeric(df['meta_proba'], errors='coerce').mean():.2f}" if "meta_proba" in df.columns else "N/A"
col4.metric("Avg meta_proba", avg_meta)

if "secteur" in df.columns:
    secteurs = st.multiselect("Filtrer par secteur", options=sorted(df["secteur"].dropna().unique()))
    if secteurs:
        df = df[df["secteur"].isin(secteurs)]

st.subheader("Signaux retenus (TAKE)")
if "decision" in df.columns:
    taken = df[df["decision"] == "TAKE"].copy()
    if "note_opportunite" in taken.columns:
        taken = taken.sort_values("note_opportunite", ascending=False)
    st.dataframe(taken.head(60), use_container_width=True)
else:
    st.info("Colonne decision absente")

if "note_opportunite" in df.columns and "decision" in df.columns:
    fig = px.histogram(df, x="note_opportunite", color="decision", nbins=20, title="Distribution Note Opportunité")
    st.plotly_chart(fig, use_container_width=True)

if "p_adverse" in df.columns and "meta_proba" in df.columns:
    fig2 = px.scatter(
        df,
        x="p_adverse",
        y="meta_proba",
        color="decision" if "decision" in df.columns else None,
        hover_data=["isin"] if "isin" in df.columns else None,
        title="Meta-proba vs Gap Risk (p_adverse)",
    )
    st.plotly_chart(fig2, use_container_width=True)

st.markdown("---")
st.caption(f"TCT {VERSION_LABEL} – Research Only – Ne constitue pas un conseil en investissement")
