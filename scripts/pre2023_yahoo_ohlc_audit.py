from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

from scripts.pre2023_yahoo_probe import (
    START, END_EXCLUSIVE, MAX_ALLOWED_DATE,
    _load_current_cache_tickers, _extract_ticker_frame,
)


def main() -> None:
    import yfinance as yf

    output = Path("outputs/pre2023_yahoo_ohlc_audit")
    output.mkdir(parents=True, exist_ok=True)
    tickers = _load_current_cache_tickers(Path("data/cache/actions"))[:120]
    anomalies=[]
    for start in range(0, len(tickers), 20):
        batch=tickers[start:start+20]
        raw=yf.download(
            tickers=batch,start=START,end=END_EXCLUSIVE,interval="1d",
            group_by="ticker",auto_adjust=False,actions=True,threads=True,
            progress=False,timeout=20,
        )
        for ticker in batch:
            frame=_extract_ticker_frame(raw,ticker)
            if frame.empty:
                continue
            idx=pd.to_datetime(frame.index,errors="coerce")
            frame=frame.loc[~idx.isna()].copy()
            idx=pd.DatetimeIndex(idx[~idx.isna()])
            if idx.tz is not None:
                idx=idx.tz_localize(None)
            frame.index=idx
            if len(frame) and frame.index.max()>MAX_ALLOWED_DATE:
                raise SystemExit(f"BLOCK_YAHOO_HOLDOUT_LEAK:{ticker}:{frame.index.max()}")
            cols={str(c).strip().lower():c for c in frame.columns}
            if not all(k in cols for k in ("open","high","low","close")):
                continue
            o=pd.to_numeric(frame[cols["open"]],errors="coerce")
            h=pd.to_numeric(frame[cols["high"]],errors="coerce")
            l=pd.to_numeric(frame[cols["low"]],errors="coerce")
            c=pd.to_numeric(frame[cols["close"]],errors="coerce")
            observed=o.notna()&h.notna()&l.notna()&c.notna()
            masks={
                "high_lt_low": observed&(h<l),
                "high_lt_open": observed&(h<o),
                "high_lt_close": observed&(h<c),
                "low_gt_open": observed&(l>o),
                "low_gt_close": observed&(l>c),
                "nonpositive": observed&((o<=0)|(h<=0)|(l<=0)|(c<=0)),
            }
            bad=pd.Series(False,index=frame.index)
            for mask in masks.values(): bad|=mask
            for dt in frame.index[bad]:
                rec={
                    "ticker":ticker,"date":dt.date().isoformat(),
                    "open":float(o.loc[dt]),"high":float(h.loc[dt]),
                    "low":float(l.loc[dt]),"close":float(c.loc[dt]),
                }
                for name,mask in masks.items(): rec[name]=bool(mask.loc[dt])
                rec["max_abs_geometry_gap"] = float(max(
                    max(0.0, o.loc[dt]-h.loc[dt]), max(0.0, c.loc[dt]-h.loc[dt]),
                    max(0.0, l.loc[dt]-o.loc[dt]), max(0.0, l.loc[dt]-c.loc[dt]),
                    max(0.0, l.loc[dt]-h.loc[dt]),
                ))
                scale=max(abs(float(o.loc[dt])),abs(float(h.loc[dt])),abs(float(l.loc[dt])),abs(float(c.loc[dt])),1e-12)
                rec["relative_gap"] = rec["max_abs_geometry_gap"]/scale
                if "stock splits" in cols:
                    split=pd.to_numeric(frame[cols["stock splits"]],errors="coerce").fillna(0)
                    rec["stock_split"] = float(split.loc[dt])
                if "dividends" in cols:
                    div=pd.to_numeric(frame[cols["dividends"]],errors="coerce").fillna(0)
                    rec["dividend"] = float(div.loc[dt])
                anomalies.append(rec)

    audit=pd.DataFrame(anomalies)
    audit.to_csv(output/"PRE2023_YAHOO_OHLC_ANOMALIES.csv",index=False)
    if audit.empty:
        summary={"anomaly_rows":0,"anomaly_tickers":0}
    else:
        summary={
            "purpose":"SOURCE_QUALITY_DIAGNOSTIC_ONLY",
            "historical_universe_certified":False,"survivorship_safe":False,
            "retuning":False,"holdout_accessed_for_prices":False,
            "anomaly_rows":int(len(audit)),"anomaly_tickers":int(audit.ticker.nunique()),
            "max_relative_gap":float(audit.relative_gap.max()),
            "median_relative_gap":float(audit.relative_gap.median()),
            "tiny_gap_le_1e_6":int((audit.relative_gap<=1e-6).sum()),
            "small_gap_le_1e_4":int((audit.relative_gap<=1e-4).sum()),
            "material_gap_gt_1e_4":int((audit.relative_gap>1e-4).sum()),
            "split_day_anomalies":int((pd.to_numeric(audit.get("stock_split",0),errors="coerce").fillna(0)!=0).sum()),
            "dividend_day_anomalies":int((pd.to_numeric(audit.get("dividend",0),errors="coerce").fillna(0)!=0).sum()),
        }
        for col in ("high_lt_low","high_lt_open","high_lt_close","low_gt_open","low_gt_close","nonpositive"):
            summary[col]=int(audit[col].astype(bool).sum())
    (output/"PRE2023_YAHOO_OHLC_AUDIT.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    main()
