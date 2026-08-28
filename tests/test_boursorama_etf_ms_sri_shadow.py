from v182.sources.boursorama_selected_etf import parse_etf_morningstar_sri_html

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


def test_morningstar_stars_and_sri_from_icons():
    parsed = parse_etf_morningstar_sri_html(ETF_COURSE_STARS)
    assert parsed["boursorama_etf_morningstar_stars"] == 4.0
    assert parsed["boursorama_etf_morningstar_parse_status"] == "OK"
    assert parsed["boursorama_etf_sri_risk"] == 4.0
    assert parsed["boursorama_etf_sri_parse_status"] == "OK"
    assert "morningstar_rating" not in parsed


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
