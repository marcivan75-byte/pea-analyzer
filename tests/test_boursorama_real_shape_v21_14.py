from v182.sources.boursorama_public import parse_action_consensus_html


def test_real_boursorama_numbered_consensus_rows_and_external_potential():
    html = """
    <html><body>
      <table>
        <tr><th>Opinion</th><th>Il y a 3 mois</th><th>Il y a 2 mois</th><th>Il y a 1 mois</th><th>Il y a 7 jours</th><th>le 3/08/2026</th></tr>
        <tr><td>1. Acheter</td><td>13</td><td>13</td><td>13</td><td>15</td><td>15</td></tr>
        <tr><td>2. Renforcer</td><td>5</td><td>5</td><td>5</td><td>4</td><td>4</td></tr>
        <tr><td>3. Conserver</td><td>3</td><td>3</td><td>3</td><td>3</td><td>4</td></tr>
        <tr><td>4. Alléger</td><td>1</td><td>1</td><td>1</td><td>0</td><td>0</td></tr>
        <tr><td>5. Vendre</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
        <tr><td>Nombre d'analystes</td><td>22</td><td>22</td><td>22</td><td>22</td><td>23</td></tr>
        <tr><td>Note médiane</td><td>1,64</td><td>1,64</td><td>1,64</td><td>1,45</td><td>1,52</td></tr>
        <tr><td>Historique des objectifs de cours médian (en EUR)</td><td>197,5</td><td>197</td><td>181,59</td><td>193,12</td><td>196,09 EUR</td></tr>
      </table>
      <div>Potentiel : 14,38%</div>
    </body></html>
    """
    fields = parse_action_consensus_html(html)
    assert fields["boursorama_n_analysts"] == 23
    assert fields["boursorama_target_median"] == 196.09
    assert fields["boursorama_target_upside_pct"] == 14.38
    assert fields["boursorama_median_note"] == 1.52
