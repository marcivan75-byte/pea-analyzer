from pathlib import Path

from etf_pack import CriterionRegistry


def test_registry_integrity():
    root = Path(__file__).resolve().parents[1]
    registry = CriterionRegistry.load(root / "reference" / "criterion_registry.json")
    registry.validate()
    assert registry.payload_sha256() == registry.document["registry_payload_sha256"]
    assert registry.document["verified_legacy_rows"] == 222
    assert registry.document["reconstructed_config_rows"] == 46


def test_unsafe_features_remain_off():
    root = Path(__file__).resolve().parents[1]
    registry = CriterionRegistry.load(root / "reference" / "criterion_registry.json")
    assert all(value is False for value in registry.document["governance"].values())
