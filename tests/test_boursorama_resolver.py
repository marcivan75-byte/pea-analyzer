from __future__ import annotations

from v182.sources.boursorama_resolver import extract_boursorama_instrument_url, resolve_boursorama_url


class _Limiter:
    def wait(self):
        return None


class _Response:
    status_code=200
    def __init__(self,text:str):
        self.text=text


class _Requests:
    def __init__(self,text:str):
        self.text=text
        self.urls=[]
    def get(self,url,*args,**kwargs):
        self.urls.append(url)
        return _Response(self.text)


def test_extract_action_url_uses_isin_context_not_market_prefix_guess():
    html='''<div>BE0003565737 KBC <a href="/cours/FF11-KBC/">KBC GR</a></div>'''
    url=extract_boursorama_instrument_url(html,isin="BE0003565737",ticker="KBC.BR",name="KBC Groupe")
    assert url=="https://www.boursorama.com/cours/FF11-KBC/"


def test_extract_etf_url_accepts_tracker_quote_route():
    html='''<div>FR0007052782 <a href="/bourse/trackers/cours/1rTCAC/">Amundi CAC 40</a></div>'''
    url=extract_boursorama_instrument_url(html,isin="FR0007052782",ticker="C40.PA",name="Amundi CAC 40")
    assert url=="https://www.boursorama.com/bourse/trackers/cours/1rTCAC/"


def test_unique_unscored_quote_is_accepted_only_for_explicit_isin_search():
    html='<a href="/cours/1rAASML/">Voir le cours</a>'
    assert extract_boursorama_instrument_url(
        html, isin="NL0010273215", ticker="ASML.AS", name="ASML", allow_single_unscored=True
    )=="https://www.boursorama.com/cours/1rAASML/"
    assert extract_boursorama_instrument_url(
        html, isin="NL0010273215", ticker="ASML.AS", name="ASML", allow_single_unscored=False
    ) is None


def test_resolver_queries_isin_first_and_keeps_explicit_url_priority():
    requests=_Requests('<div>NL0011821202 INGA <a href="/cours/1rAINGA/">ING GROUP</a></div>')
    row={"isin":"NL0011821202","yahoo_ticker":"INGA.AS","name":"ING GROUP"}
    resolved=resolve_boursorama_url(row,requests,_Limiter())
    assert resolved=="https://www.boursorama.com/cours/1rAINGA/"
    assert "NL0011821202" in requests.urls[0]

    explicit={**row,"boursorama_url":"https://www.boursorama.com/cours/custom/"}
    before=len(requests.urls)
    assert resolve_boursorama_url(explicit,requests,_Limiter())==explicit["boursorama_url"]
    assert len(requests.urls)==before
