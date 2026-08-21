from pathlib import Path

from etf_pack import CriterionRegistry, __version__


def test_package_version_is_r4():
    assert __version__ == "21.18.1R4"


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


def test_registry_ordered_names_hash():
    registry = CriterionRegistry.load(Path(__file__).resolve().parents[1] / "reference" / "criterion_registry.json")
    assert registry.ordered_names_sha256() == registry.document["expected_ordered_names_sha256"]


def test_registry_filter_by_horizon():
    registry = CriterionRegistry.load(Path(__file__).resolve().parents[1] / "reference" / "criterion_registry.json")
    assert registry.filter(horizon="MT")
    assert all(row["horizon"] == "MT" for row in registry.filter(horizon="MT"))


def test_registry_normalized_weights_sum_to_one():
    registry = CriterionRegistry.load(Path(__file__).resolve().parents[1] / "reference" / "criterion_registry.json")
    assert abs(sum(registry.weights_normalized().values()) - 1.0) < 1e-12


def test_registry_normalized_weights_preserve_ids():
    registry = CriterionRegistry.load(Path(__file__).resolve().parents[1] / "reference" / "criterion_registry.json")
    assert set(registry.weights_normalized()) == {row["criterion_id"] for row in registry.criteria}
