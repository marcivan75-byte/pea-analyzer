from v182.hebdo.fp_early_exit import FPEarlyExit


def bar(close, low=None, open_px=100.0, **extra):
    d={"open":open_px,"low":close if low is None else low,"close":close}
    d.update(extra)
    return d


def test_fail_fast_j2_is_post_entry_and_exact_day_two():
    e=FPEarlyExit(enabled_rules={"STOP","FAIL_FAST_J2"})
    x=bar(97.0,low=96.5)
    assert e.check_exit(100.0,x,1)[0] is False
    yes,reason,ret=e.check_exit(100.0,x,2)
    assert yes is True and reason.startswith("FAIL_FAST_J2") and abs(ret+0.03)<1e-12


def test_disabled_fail_fast_does_not_fire():
    e=FPEarlyExit(enabled_rules={"STOP"})
    assert e.check_exit(100.0,bar(97.0,low=96.5),2)[0] is False


def test_structure_entry_day_uses_known_signal_and_confirmation_levels():
    e=FPEarlyExit(enabled_rules={"STOP","STRUCTURE_INVALID_ENTRY_DAY"})
    x=bar(98.0,low=97.5,signal_level=99.0,confirmation_low=98.5)
    yes,reason,_=e.check_exit(100.0,x,1)
    assert yes is True and reason.startswith("STRUCTURE_INVALID_ENTRY_DAY")


def test_trail_break_even_requires_prior_peak():
    e=FPEarlyExit(enabled_rules={"STOP","TRAIL_BE"})
    x=bar(100.5,low=100.0,peak_pnl_prior=0.04)
    yes,reason,ret=e.check_exit(100.0,x,5)
    assert yes is True and reason.startswith("TRAIL_BE") and abs(ret-0.01)<1e-12


def test_capitulation_rule_is_disabled_when_not_selected():
    x=bar(98.0,low=97.0,vol_z=6.0)
    e=FPEarlyExit(enabled_rules={"STOP"})
    assert e.check_exit(100.0,x,3)[0] is False
