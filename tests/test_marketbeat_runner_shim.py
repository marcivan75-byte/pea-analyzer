from v182.decision import marketbeat_overlay as core
from v182.decision import marketbeat_overlay_runner as runner


def test_marketbeat_runner_delegates_to_canonical_module():
    assert runner.apply_marketbeat_overlay is core.apply_marketbeat_overlay
    assert runner.main is core.main
