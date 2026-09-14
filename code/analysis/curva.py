"""Dump forense: curva de 90 dias tal como la construye finance/view.py.

Uso:  python3 analysis/curva.py request_05 [request_14 ...]
"""
from __future__ import annotations
import sys, os, json
from decimal import Decimal
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
sys.path.insert(0, CODE)

import loader
from finance import view as fv
from finance import params
from extraction.facts import load_facts

def show(req_id, ds, facts, expected=None):
    reqs = {r.request_id: r for r in loader.sample_requests()}
    req = reqs[req_id]
    prof = ds.profiles[req.user_id]
    rec = fv.reconstruct(ds, req, facts)
    pts, lo, lo_day = fv.curve(rec)
    print("="*100)
    print(f"{req_id}  {req.user_id}  request_date={req.request_date}  end={rec.end}")
    print(f"  home={prof.home_currency} balance={prof.current_available_balance} "
          f"min_keep={prof.minimum_balance_to_keep}")
    print(f"  requested={req.requested_amount} deadline={req.desired_completion_date} "
          f"partial={req.allows_partial_payment}")
    if expected is not None:
        print(f"  ESPERADO safe={expected}  ->  min_balance_90d objetivo = "
              f"{Decimal(expected)+prof.minimum_balance_to_keep}")
    print(f"  OBTENIDO min={lo} @ {lo_day}  headroom={max(Decimal(0),lo-prof.minimum_balance_to_keep)} "
          f"safe={min(req.requested_amount, max(Decimal(0), lo-prof.minimum_balance_to_keep))}")
    print("-"*100)
    print("SERIES DETECTADAS:")
    for s in rec.series:
        print(f"   {s.direction:6s} {s.event_type:16s} {s.category:22s} "
              f"{s.period_kind}:{s.period:<4} amt={s.amount:>14.2f} occ={s.occurrences} "
              f"last={s.last_day} varies={s.varies} sample={s.sample.event_id} "
              f"| {s.sample.description[:40]}")
    print("-"*100)
    print("NOTAS:")
    for n in rec.notes:
        print("   ", n)
    print("-"*100)
    print("FLUJOS (dia, delta, saldo_despues, nota, event_id):")
    bal = prof.current_available_balance
    print(f"   {rec.start}  {'INICIO':>16}  saldo={bal:>16.2f}")
    for f in sorted(rec.flows, key=lambda f:(f.day, f.note)):
        bal += f.delta
        mark = "  <<< MIN" if (f.day == lo_day) else ""
        print(f"   {f.day}  {f.delta:>16.2f}  saldo={bal:>16.2f}  {f.note[:52]:<52} "
              f"{f.event_id or ''}{mark}")
    print()

if __name__ == "__main__":
    ds = loader.load()
    facts = load_facts(ds)
    exp = {"request_05":"737.00","request_14":"597.74","request_15":"83.05",
           "request_10":"12700.00","request_13":"433.40","request_25":"1425000.00"}
    for rid in sys.argv[1:]:
        show(rid, ds, facts, exp.get(rid))
