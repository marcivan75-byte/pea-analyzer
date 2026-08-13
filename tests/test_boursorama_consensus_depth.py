from __future__ import annotations

from v182.sources.boursorama_consensus_depth import parse_consensus_depth_html


def test_consensus_depth_extracts_bullish_bearish_and_raw_firm_list():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/consensus/1rPAKE/" /></head><body>
      <div>ISIN FR0010313833</div>
      <table><thead><tr><th>Opinion</th><th>Il y a 1 mois</th><th>le 28/07/2026</th></tr></thead><tbody>
        <tr><td>1. Acheter</td><td>7</td><td>6</td></tr>
        <tr><td>2. Renforcer</td><td>0</td><td>0</td></tr>
        <tr><td>3. Conserver</td><td>7</td><td>7</td></tr>
        <tr><td>4. Alléger</td><td>0</td><td>0</td></tr>
        <tr><td>5. Vendre</td><td>3</td><td>3</td></tr>
        <tr><td>Nombre d'analystes</td><td>17</td><td>16</td></tr>
      </tbody></table>
      <p>Liste des cabinets d'analystes ayant suivi la valeur au moins une fois dans l'année : Berenberg, Deutsche Bank Research, Jefferies, Oddo BHF Corporates & Markets, Societe Generale</p>
      <p>NB : certains bureaux d'analyses ont souhaité conserver l'anonymat</p>
      <p>Note médiane des analystes au 28.07.2026</p>
    </body></html>
    """
    obs, failures, stats = parse_consensus_depth_html(
        html, {"FR0010313833"}, "arkema.html"
    )
    assert failures == []
    assert stats["matched_rows"] == 1
    fields = {row["field"]: row for row in obs}
    assert fields["boursorama_consensus_bullish_count"]["value"] == 6
    assert fields["boursorama_consensus_neutral_count"]["value"] == 7
    assert fields["boursorama_consensus_bearish_count"]["value"] == 3
    assert fields["boursorama_consensus_bullish_pct"]["value"] == 37.5
    assert fields["boursorama_consensus_bearish_pct"]["value"] == 18.75
    assert fields["boursorama_consensus_net_bullish_balance_pct"]["value"] == 18.75
    assert "Berenberg" in fields["boursorama_analyst_firms_list_raw"]["value"]
    assert "Oddo BHF" in fields["boursorama_analyst_firms_list_raw"]["value"]
    assert fields["boursorama_analyst_firms_anonymity_warning"]["value"] is True
    assert fields["boursorama_consensus_bullish_pct"]["evidence_level"] == "B"


def test_consensus_depth_does_not_split_comma_bearing_firm_names_into_fake_count():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/consensus/TEST/" /></head><body>
      <div>ISIN FR0010313833</div>
      <table><tbody>
        <tr><td>1. Acheter</td><td>1</td></tr>
        <tr><td>3. Conserver</td><td>1</td></tr>
        <tr><td>5. Vendre</td><td>0</td></tr>
        <tr><td>Nombre d'analystes</td><td>2</td></tr>
      </tbody></table>
      <p>Liste des cabinets d'analystes ayant suivi la valeur au moins une fois dans l'année : Crespi, Hardt, LLC, Jefferies</p>
      <p>Note médiane des analystes</p>
    </body></html>
    """
    obs, failures, _ = parse_consensus_depth_html(html, {"FR0010313833"})
    assert failures == []
    fields = {row["field"]: row["value"] for row in obs}
    assert fields["boursorama_analyst_firms_list_raw"] == "Crespi, Hardt, LLC, Jefferies"
    assert "boursorama_analyst_firms_count" not in fields
