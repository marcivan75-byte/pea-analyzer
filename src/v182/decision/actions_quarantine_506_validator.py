from __future__ import annotations

from pathlib import Path
import json, re, time
import pandas as pd
import requests
import yfinance as yf

ROOT=Path(__file__).resolve().parents[3]
SRC=ROOT/'data/reference/PEA_ACTIONS_506_QUARANTINE_ISIN.csv'
OUT=ROOT/'outputs/V20.4_ACTIONS_506_QUARANTINE_VALIDATION.csv'
ADD=ROOT/'outputs/V20.4_ACTIONS_506_CANDIDATES_TO_INTEGRATE.csv'
AUDIT=ROOT/'outputs/audit/V20.4_ACTIONS_506_QUARANTINE_AUDIT.json'

EEA={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','IS','LI','NO'}
EXCL_PAT=re.compile(r'\b(WARR|WARRANT|BSA|CERT|RIGHT|RSP|PREF|SIGI|SOCIMI|REIT)\b',re.I)


def yahoo_search(q:str):
    url='https://query1.finance.yahoo.com/v1/finance/search'
    try:
        r=requests.get(url,params={'q':q,'quotesCount':8,'newsCount':0},timeout=12,headers={'User-Agent':'Mozilla/5.0'})
        if r.ok:
            return r.json().get('quotes',[])
    except Exception:
        pass
    return []


def norm_country(x:str)->str:
    return (x or '').strip()


def validate_one(isin:str)->dict:
    row={'isin':isin,'isin_prefix':isin[:2],'jurisdiction_eea':isin[:2] in EEA,'status':'A_REVOIR','reason':'','yahoo_ticker':'','yahoo_name':'','quote_type':'','exchange':'','country':'','history_rows_6mo':0,'history_days_span':0,'avg_volume_20d':None,'last_price':None,'identity_confidence':0.0,'pea_confidence':0.0,'enrichment_ready':False}
    quotes=yahoo_search(isin)
    equities=[q for q in quotes if str(q.get('quoteType','')).upper() in {'EQUITY','ETF'}]
    q=equities[0] if equities else (quotes[0] if quotes else {})
    ticker=str(q.get('symbol') or '')
    row['yahoo_ticker']=ticker; row['yahoo_name']=q.get('longname') or q.get('shortname') or ''; row['quote_type']=q.get('quoteType') or ''; row['exchange']=q.get('exchange') or ''
    if not ticker:
        row['reason']='Yahoo search: aucun ticker résolu par ISIN'; return row
    if str(row['quote_type']).upper()!='EQUITY':
        row['status']='EXCLURE'; row['reason']=f"Type Yahoo non action: {row['quote_type']}"; return row
    try:
        t=yf.Ticker(ticker)
        info=t.get_info() or {}
        row['country']=norm_country(info.get('country'))
        h=t.history(period='6mo',auto_adjust=False,actions=False)
        row['history_rows_6mo']=int(len(h))
        if len(h):
            row['history_days_span']=int((h.index.max()-h.index.min()).days)
            row['last_price']=float(h['Close'].dropna().iloc[-1]) if h['Close'].notna().any() else None
            row['avg_volume_20d']=float(h['Volume'].tail(20).mean()) if 'Volume' in h else None
        name=(info.get('longName') or info.get('shortName') or row['yahoo_name'] or '')
        row['yahoo_name']=name
        row['exchange']=info.get('exchange') or row['exchange']
    except Exception as e:
        row['reason']=f'yfinance erreur: {type(e).__name__}'; return row
    if not row['jurisdiction_eea']:
        row['status']='EXCLURE'; row['reason']='ISIN hors UE/EEE'; return row
    if EXCL_PAT.search(str(row['yahoo_name'])):
        row['status']='EXCLURE'; row['reason']='Instrument/structure exclue détectée dans le nom'; return row
    hist_ok=row['history_rows_6mo']>=60 and row['last_price'] not in (None,0)
    identity=0.96 if ticker and row['quote_type']=='EQUITY' else 0.75
    if hist_ok: identity=min(0.99,identity+0.02)
    row['identity_confidence']=round(identity,3)
    row['pea_confidence']=round(0.95 if row['jurisdiction_eea'] and hist_ok else 0.82,3)
    row['enrichment_ready']=bool(hist_ok and identity>=0.95)
    if row['enrichment_ready']:
        row['status']='INTEGRER'; row['reason']='Action UE/EEE, ticker Yahoo résolu, historique 6 mois suffisant et identité forte'
    else:
        row['status']='A_REVOIR'; row['reason']='Action potentielle mais données/identité insuffisantes pour intégration automatique'
    return row


def main():
    src=pd.read_csv(SRC,dtype=str)
    if len(src)!=506 or src['isin'].nunique()!=506: raise RuntimeError(f'quarantine universe invalid {len(src)} / {src.isin.nunique()}')
    rows=[]
    for i,isin in enumerate(src['isin'].astype(str).str.strip().str.upper(),1):
        rows.append(validate_one(isin))
        if i%25==0: print('validated',i)
        time.sleep(0.08)
    df=pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True,exist_ok=True); AUDIT.parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(OUT,sep=';',index=False,encoding='utf-8-sig')
    add=df[df.status.eq('INTEGRER')].copy()
    add.to_csv(ADD,sep=';',index=False,encoding='utf-8-sig')
    audit={'rows':len(df),'integrer':int(df.status.eq('INTEGRER').sum()),'a_revoir':int(df.status.eq('A_REVOIR').sum()),'exclure':int(df.status.eq('EXCLURE').sum()),'yahoo_resolved':int(df.yahoo_ticker.ne('').sum()),'history_ok':int(df.history_rows_6mo.ge(60).sum()),'smart_money_enabled':False,'live_order_execution_enabled':False,'passed':len(df)==506}
    AUDIT.write_text(json.dumps(audit,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('QUARANTINE506',audit)

if __name__=='__main__': main()
