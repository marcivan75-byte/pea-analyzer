import pandas as pd

from scripts.materialize_v4_frozen_upstream import run


def test_frozen_overlay_preserves_candidate_set(monkeypatch, tmp_path):
    source = tmp_path / "source.csv"
    pd.DataFrame([{"isin": "A", "score": 80}]).to_csv(source, sep=";", index=False, encoding="utf-8-sig")
    monkeypatch.setattr(
        "scripts.materialize_v4_frozen_upstream.govern_existing_frame",
        lambda frame, root: frame.assign(CI_CONFIDENCE_SCORE_V22_2_1=70),
    )
    payload = run(source, tmp_path)
    assert payload["status"] == "PASS"
    assert payload["candidate_set_changed"] is False
    output = pd.read_csv(tmp_path / payload["output"], sep=";")
    assert output["isin"].tolist() == ["A"]


def test_frozen_overlay_rejects_unbounded_input(monkeypatch, tmp_path):
    source = tmp_path / "source.csv"
    pd.DataFrame([{"isin": str(index)} for index in range(3)]).to_csv(source, sep=";", index=False)
    monkeypatch.setattr(
        "scripts.materialize_v4_frozen_upstream.govern_existing_frame",
        lambda frame, root: frame,
    )
    try:
        run(source, tmp_path, maximum_rows=2)
    except ValueError as exc:
        assert str(exc) == "FROZEN_UPSTREAM_NOT_BOUNDED"
    else:
        raise AssertionError("unbounded input accepted")
