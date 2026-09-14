"""Curva del lab (independiente de finance/)."""
from __future__ import annotations
import sys, os
from decimal import Decimal as D
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import loader
from analysis.lab import Cfg, data, curva
from dataclasses import replace

def main(rids, **kw):
    c = replace(Cfg(), **kw); ds, facts = data()
    gt={r["request_id"]:r for r in loader.load_samples()}
    reqs={r.request_id:r for r in loader.sample_requests()}
    for rid in rids:
        req=reqs[rid]
        prof,start,end,fl,ser,pts,lo,lo_day = curva(ds,req,facts,c)
        exp=D(gt[rid]["amount_safe_to_pay"]); tgt=exp+prof.minimum_balance_to_keep
        print("="*104)
        print(f"{rid} {req.user_id} {prof.home_currency} as_of={start} end={end} bal={prof.current_available_balance} "
              f"min={prof.minimum_balance_to_keep} req={req.requested_amount} deadline={req.desired_completion_date}")
        print(f"  GT safe={exp} -> min objetivo={tgt} | lab min={lo:.2f} @{lo_day} delta={lo-tgt:.2f}")
        print("  SERIES:")
        for s in ser:
            print(f"    {s.direction:6s}{s.etype:14s}{s.cat:20s}{s.kind}:{s.period:<4}amt={s.amount:>16.2f} "
                  f"occ={s.occ} last={s.last_day} var={s.varies} {s.sample.event_id} {s.sample.description[:34]}")
        bal=prof.current_available_balance
        print(f"  {start}  {'INICIO':>18}  saldo={bal:>18.2f}")
        for f in fl:
            bal+=f.delta
            print(f"  {f.day}  {f.delta:>18.2f}  saldo={bal:>18.2f}  {f.note[:44]:<44}{f.eid or ''}"
                  f"{'   <<< MIN' if f.day==lo_day else ''}")

if __name__=="__main__":
    main(sys.argv[1:])
