from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pandas as pd
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "v182" / "data" / "pre2023_eodhd_inventory.py"
_spec = spec_from_file_location("pea_pre2023_eodhd_inventory", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)

load_exchange_scope = _mod.load_exchange_scope
normalize_symbol_rows = _mod.normalize_symbol_rows
merge_active_delisted = _mod.merge_active_delisted


def test_exchange_scope_requires_provenance(tmp_path):
    p = tmp_path / "scope.csv"
    p.write_text("eodhd_exchange,mic,country,scope_evidence\nPA,XPAR,France,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="blank mandatory"):
        load_exchange_scope(p)


def test_normalizer_preserves_old_provider_symbol():
    df = normalize_symbol_rows(
        [{"Code": "ABC_old", "Name": "Historical Co", "Type": "Common Stock", "Isin": "FR0000000001", "Currency": "EUR"}],
        exchange="PA", mic="XPAR", country="France", status="delisted", scope_evidence="provider-exchange-scope",
    )
    assert df.loc[0, "eodhd_symbol"] == "ABC_old.PA"
    assert df.loc[0, "provider_status"] == "delisted"


def test_merge_requires_delisted_names():
    active = normalize_symbol_rows(
        [{"Code": "AAA"}], exchange="PA", mic="XPAR", country="France", status="active", scope_evidence="scope"
    )
    empty_dead = pd.DataFrame(columns=_mod.OUTPUT_COLUMNS)
    with pytest.raises(ValueError, match="SURVIVORSHIP"):
        merge_active_delisted(active, empty_dead)


def test_merge_rejects_active_delisted_collision():
    active = normalize_symbol_rows(
        [{"Code": "AAA"}], exchange="PA", mic="XPAR", country="France", status="active", scope_evidence="scope"
    )
    dead = normalize_symbol_rows(
        [{"Code": "AAA"}], exchange="PA", mic="XPAR", country="France", status="delisted", scope_evidence="scope"
    )
    with pytest.raises(ValueError, match="COLLISION"):
        merge_active_delisted(active, dead)


def test_merge_accepts_active_and_delisted():
    active = normalize_symbol_rows(
        [{"Code": "AAA", "Isin": "FR0000000001"}], exchange="PA", mic="XPAR", country="France", status="active", scope_evidence="scope"
    )
    dead = normalize_symbol_rows(
        [{"Code": "BBB_old", "Isin": "FR0000000002"}], exchange="PA", mic="XPAR", country="France", status="delisted", scope_evidence="scope"
    )
    out = merge_active_delisted(active, dead)
    assert set(out["provider_status"]) == {"active", "delisted"}
    assert len(out) == 2
