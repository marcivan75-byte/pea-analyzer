from __future__ import annotations

from datetime import date

import pandas as pd

from v182.sources.boursorama_company_calendar import parse_company_calendar_html


def test_company_calendar_maps_exact_names_and_keeps_event_context_shadow():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/actualites/calendriers/societes-cotees" /></head><body>
      <h3>jeu 13 août</h3>
      <table><thead><tr><th>heure</th><th>société</th><th>évènement</th></tr></thead><tbody>
        <tr><td>08:00</td><td>AIRBUS</td><td>Résultats du 1er semestre</td></tr>
      </tbody></table>
      <h3>jeu 20 août</h3>
      <table><thead><tr><th>heure</th><th>société</th><th>évènement</th></tr></thead><tbody>
        <tr><td>09:00</td><td>LVMH</td><td>Réunion d'analystes semestrielle</td></tr>
        <tr><td>18:00</td><td>AIRBUS</td><td>Chiffre d'affaires 2ème trimestre</td></tr>
      </tbody></table>
    </body></html>
    """
    actions = pd.DataFrame([
        {"isin": "NL0000235190", "name": "AIRBUS"},
        {"isin": "FR0000121014", "name": "LVMH"},
    ])
    obs, failures, stats = parse_company_calendar_html(
        html, actions, "calendar.html", today=date(2026, 8, 13)
    )
    assert failures == []
    assert stats["matched_rows"] == 2
    air = {row["field"]: row for row in obs if row["isin"] == "NL0000235190"}
    lvmh = {row["field"]: row for row in obs if row["isin"] == "FR0000121014"}
    assert air["boursorama_next_corporate_event_date"]["value"] == "2026-08-13"
    assert air["boursorama_days_to_corporate_event"]["value"] == 0
    assert air["boursorama_next_corporate_event_class"]["value"] == "RESULTS"
    assert air["boursorama_corporate_events_visible_count"]["value"] == 2
    assert lvmh["boursorama_next_corporate_event_date"]["value"] == "2026-08-20"
    assert lvmh["boursorama_next_corporate_event_class"]["value"] == "ANALYST_INVESTOR_MEETING"
    assert lvmh["boursorama_corporate_event_within_7d_flag"]["value"] == 1.0
    assert air["boursorama_next_corporate_event_date"]["evidence_level"] == "B"
    assert "days_to_earnings" not in air
    assert "earnings_within_7d_flag" not in air


def test_company_calendar_does_not_fuzzy_match_unknown_company():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/actualites/calendriers/societes-cotees" /></head><body>
      <h3>jeu 13 août</h3>
      <table><thead><tr><th>heure</th><th>société</th><th>évènement</th></tr></thead><tbody>
        <tr><td>08:00</td><td>AIRBUS HOLDING INCONNUE</td><td>Résultats du 1er semestre</td></tr>
      </tbody></table>
    </body></html>
    """
    actions = pd.DataFrame([{"isin": "NL0000235190", "name": "AIRBUS"}])
    obs, failures, stats = parse_company_calendar_html(
        html, actions, "calendar.html", today=date(2026, 8, 13)
    )
    assert obs == []
    assert failures == []
    assert stats["matched_rows"] == 0
