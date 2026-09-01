from datetime import datetime, timezone

from v182.sources.boursorama_consensus_history import (
    current_and_revision,
    parse_factset_consensus_history,
)

HTML = """
<table>
<tr><th>Consensus</th><th>3 mois</th><th>2 mois</th><th>1 mois</th><th>7 jours</th><th>22/08/2026</th></tr>
<tr><td>Acheter</td><td>13</td><td>13</td><td>13</td><td>15</td><td>15</td></tr>
<tr><td>Renforcer</td><td>5</td><td>5</td><td>5</td><td>4</td><td>4</td></tr>
<tr><td>Conserver</td><td>3</td><td>3</td><td>3</td><td>3</td><td>4</td></tr>
<tr><td>Alléger</td><td>1</td><td>1</td><td>1</td><td>0</td><td>0</td></tr>
<tr><td>Vendre</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>Note médiane</td><td>1,64</td><td>1,64</td><td>1,64</td><td>1,45</td><td>1,52</td></tr>
<tr><td>Objectif de cours médian</td><td>197,50</td><td>197,00</td><td>181,59</td><td>193,12</td><td>196,09</td></tr>
<tr><td>Potentiel</td><td>10,0%</td><td>10,0%</td><td>8,0%</td><td>12,0%</td><td>14,38%</td></tr>
</table>
"""
CAPTURE = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)


def history():
    return parse_factset_consensus_history(HTML, captured_at=CAPTURE)


def test_audit73_preserves_all_five_factset_observations():
    assert [r["period_label"] for r in history()] == ["3 mois", "2 mois", "1 mois", "7 jours", "22/08/2026"]


def test_audit73_relative_periods_never_receive_artificial_dates():
    h = history()
    assert all(r["as_of_date"] is None for r in h[:4])
    assert all(r["artificial_date_assigned"] is False for r in h)
    assert h[4]["as_of_date"] == "2026-08-22"


def test_audit73_keeps_real_capture_availability_timestamp():
    assert {r["available_at"] for r in history()} == {"2026-08-22T18:00:00+00:00"}


def test_audit73_preserves_target_and_published_upside_for_each_column():
    h = history()
    assert [r["target_median"] for r in h] == [197.5, 197.0, 181.59, 193.12, 196.09]
    assert [r["published_upside_pct"] for r in h] == [10.0, 10.0, 8.0, 12.0, 14.38]


def test_audit73_preserves_analyst_coverage_and_buy_hold_sell_distribution():
    current = history()[-1]
    assert current["n_analysts"] == 23
    assert current["buy_n"] == 19
    assert current["hold_n"] == 4
    assert current["sell_n"] == 0


def test_audit73_preserves_consensus_semantics():
    current = history()[-1]
    assert current["consensus"] == "BUY"
    assert current["consensus_score"] == round((15 * 5 + 4 * 4 + 4 * 3) / 23, 4)


def test_audit73_revision_uses_same_capture_without_backdating():
    h = history()
    derived = current_and_revision(h)
    expected = round((h[-1]["consensus_score"] - h[2]["consensus_score"]) * 20, 4)
    assert derived["consensus_delta_4w"] == expected
    assert derived["available_at"] == "2026-08-22T18:00:00+00:00"
