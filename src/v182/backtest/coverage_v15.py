"""CLI for CDC PIT V15 data qualification. No performance optimization is performed."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from .pit_v15 import qualify_trades, write_audit_outputs


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--trades',required=True,help='Historical trades CSV with isin,date_signal,close')
    p.add_argument('--pit-table',required=True,help='Qualified candidate PIT table CSV')
    p.add_argument('--mapping',required=True,help='Historical ISIN/ticker validity mapping CSV')
    p.add_argument('--current-potential',required=True,help='Current potential CSV used only for anti-leak correlation test')
    p.add_argument('--outdir',default='outputs/backtest/pit_v15')
    args=p.parse_args()
    trades=pd.read_csv(args.trades)
    pit=pd.read_csv(args.pit_table)
    mapping=pd.read_csv(args.mapping)
    current=pd.read_csv(args.current_potential)
    audit,report=qualify_trades(trades,pit,mapping,current)
    write_audit_outputs(audit,report,Path(args.outdir))
    print(report)
    if not report['performance_backtest_authorized']:
        raise SystemExit(2)

if __name__=='__main__':
    main()
