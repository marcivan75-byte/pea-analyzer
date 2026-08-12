from __future__ import annotations

import math

from v182.reporting.committee_performance_v21_4 import _cash_investment_room


def test_cash_room_preserves_buffer_after_fees_at_binding_limit():
    cash=30_000.0
    equity=100_000.0
    buffer_pct=20.0
    fee=0.0025
    gross=_cash_investment_room(cash,equity,buffer_pct,fee)

    post_cash=cash-gross*(1.0+fee)
    post_equity=equity-gross*fee
    required_cash=(buffer_pct/100.0)*post_equity

    assert gross>0
    assert math.isclose(post_cash,required_cash,rel_tol=0,abs_tol=1e-8)
    assert post_cash+1e-8>=required_cash


def test_cash_room_is_zero_when_cash_is_already_at_buffer():
    assert _cash_investment_room(20_000.0,100_000.0,20.0,0.0025)==0.0


def test_zero_fee_reduces_to_simple_cash_minus_buffer_room():
    room=_cash_investment_room(35_000.0,100_000.0,20.0,0.0)
    assert math.isclose(room,15_000.0,rel_tol=0,abs_tol=1e-12)


def test_cash_room_clamps_invalid_buffer_and_negative_fee_conservatively():
    assert _cash_investment_room(50_000.0,100_000.0,120.0,-0.5)==0.0
