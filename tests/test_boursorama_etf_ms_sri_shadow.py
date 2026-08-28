from v182.sources.boursorama_selected_etf import (
    merge_ms_sri_fields,
    parse_etf_morningstar_sri_html,
    parse_etf_palmares_rows,
)

ETF_COURSE_STARS = """
<html><body>
<div class="c-notation-morningstar">
  Notation morningstar (1) du 31 juil. 2026
  <span class="c-star c-star--on"></span>
  <span class="c-star c-star--on"></span>
  <span class="c-star c-star--on"></span>
  <span class="c-star c-star--on"></span>
  <span class="c-star"></span>
</div>
<div class="c-sri">Risque du fonds (SRI) <span>4</span>/7</div>
</body></html>
"""

ETF_EDEU_LIVE = """
<html><body>
<div class="c-analysis__morningstar">
  <p>Notation morningstar (1) du 31 juil. 2026</p>
  <fieldset class="c-rating" aria-label="Notation Morningstar 4 étoiles sur 5">
    <input class="c-rating__check" type="radio" value="5" disabled="disabled"/>
    <input class="c-rating__check" type="radio" value="4" checked="checked" disabled="disabled"/>
    <input class="c-rating__check" type="radio" value="3" disabled="disabled"/>
  </fieldset>
</div>
<div class="c-gauge" data-gauge-steps="7" data-gauge-current-step="4">
  <p>Risque du fonds (SRI) /7</p>
</div>
</body></html>
"""

ETF_PALMARES = """
<html><body>
<table>
<tr>
  <td><a href="/bourse/trackers/cours/1rTEDEU/">BNP Easy Dividend Europe</a></td>
  <td>/7<div class="c-gauge" data-gauge-steps="7" data-gauge-current-step="4"></div></td>
  <td>4
    <fieldset class="c-rating" aria-label="Notation Morningstar 4 étoiles sur 5"></fieldset>
  </td>
</tr>
</table>
</body></html>
"""


def test_morningstar_stars_and_sri_from_icons():
    parsed = parse_etf_morningstar_sri_html(ETF_COURSE_STARS)
    assert parsed["boursorama_etf_morningstar_stars"] == 4.0
    assert parsed["boursorama_etf_morningstar_parse_status"] == "OK"
    assert parsed["boursorama_etf_sri_risk"] == 4.0
    assert parsed["boursorama_etf_sri_parse_status"] == "OK"
    assert "morningstar_rating" not in parsed


def test_edeu_live_attributes():
    parsed = parse_etf_morningstar_sri_html(ETF_EDEU_LIVE)
    assert parsed["boursorama_etf_morningstar_stars"] == 4.0
    assert parsed["boursorama_etf_morningstar_parse_source"] == "ARIA_LABEL"
    assert parsed["boursorama_etf_sri_risk"] == 4.0
    assert parsed["boursorama_etf_sri_parse_source"] == "GAUGE_ATTR"


def test_five_radios_are_not_counted_as_five_stars():
    html = """<html><body>
    <fieldset class="c-rating" aria-label="Notation Morningstar 4 étoiles sur 5">
      <input class="c-rating__check" value="5"/><input class="c-rating__check" value="4" checked="checked"/>
      <input class="c-rating__check" value="3"/><input class="c-rating__check" value="2"/><input class="c-rating__check" value="1"/>
    </fieldset>
    </body></html>"""
    parsed = parse_etf_morningstar_sri_html(html)
    assert parsed["boursorama_etf_morningstar_stars"] == 4.0


def test_morningstar_block_without_icons_is_unresolved():
    parsed = parse_etf_morningstar_sri_html(
        "<html><body>Notation morningstar (1) du 31 juil. 2026 Risque du fonds (SRI) /7</body></html>"
    )
    assert "boursorama_etf_morningstar_stars" not in parsed
    assert parsed["boursorama_etf_morningstar_parse_status"] == "ICONS_UNRESOLVED"


def test_sri_alone_does_not_create_stars():
    parsed = parse_etf_morningstar_sri_html(
        "<html><body><div>Risque du fonds (SRI) 4/7</div></body></html>"
    )
    assert parsed["boursorama_etf_sri_risk"] == 4.0
    assert "boursorama_etf_morningstar_stars" not in parsed
    assert parsed["boursorama_etf_morningstar_parse_status"] == "BLOCK_MISSING"


def test_explicit_star_text_fallback():
    parsed = parse_etf_morningstar_sri_html(
        "<html><body>Notation morningstar 4 etoiles du 31 juil. 2026</body></html>"
    )
    assert parsed["boursorama_etf_morningstar_stars"] == 4.0
    assert parsed["boursorama_etf_morningstar_parse_status"] == "OK"


def test_missing_block():
    parsed = parse_etf_morningstar_sri_html("<html><body>PEA</body></html>")
    assert parsed["boursorama_etf_morningstar_parse_status"] == "BLOCK_MISSING"
    assert "boursorama_etf_morningstar_stars" not in parsed


def test_palmares_numeric_row():
    rows = parse_etf_palmares_rows(ETF_PALMARES)
    assert rows["1rTEDEU"]["boursorama_etf_morningstar_stars"] == 4.0
    assert rows["1rTEDEU"]["boursorama_etf_sri_risk"] == 4.0
    assert rows["1rTEDEU"]["boursorama_etf_morningstar_parse_source"] == "PALMARES_NUMERIC"


def test_merge_keeps_ok_against_later_missing_block():
    first = parse_etf_morningstar_sri_html(ETF_EDEU_LIVE)
    later = parse_etf_morningstar_sri_html("<html><body>PEA</body></html>")
    merged = merge_ms_sri_fields(first, later)
    assert merged["boursorama_etf_morningstar_stars"] == 4.0
    assert merged["boursorama_etf_morningstar_parse_status"] == "OK"
    assert merged["boursorama_etf_sri_risk"] == 4.0
