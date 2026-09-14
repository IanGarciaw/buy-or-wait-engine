"""Ajuste inverso: que multiplicador k sobre TODO el gasto proyectado reproduce el GT."""
from __future__ import annotations
import sys, os
from decimal import Decimal as D
from dataclasses import replace
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import loader
from analysis.lab import Cfg, data, curva
from datetime import timedelta

def min_con_k(ds, req, facts, c, k, solo_variables=False, solo_recurrentes=True):
    prof,start,end,fl,ser,pts,lo,lo_day = curva(ds,req,facts,c)
    var_keys = {s.key for s in ser if s.varies}
    bal = prof.current_available_balance; day=start; i=0
    fl2=[]
    for f in fl:
        d=f.delta
        if d < 0 and (not solo_recurrentes or f.note.startswith("recurrente")):
            if not solo_variables or (f.key in var_keys):
                d = d*k
        fl2.append((f.day,d))
    fl2.sort()
    out=[];  bal=prof.current_available_balance; i=0; day=start
    while day<=end:
        while i<len(fl2) and fl2[i][0]<=day:
            bal+=fl2[i][1]; i+=1
        out.append((day,bal)); day+=timedelta(days=1)
    d,b=min(out,key=lambda t:(t[1],t[0]))
    return b,d

def fit(ds,req,facts,c,tgt,**kw):
    lo_k,hi_k=D("0.2"),D("3.0")
    for _ in range(60):
        mid=(lo_k+hi_k)/2
        b,_=min_con_k(ds,req,facts,c,mid,**kw)
        if b > tgt: lo_k=mid
        else: hi_k=mid
    return (lo_k+hi_k)/2

def main():
    c=Cfg(); ds,facts=data()
    gt={r["request_id"]:r for r in loader.load_samples()}
    print(f"{'req':<11}{'trough_day':>4} {'k_todo':>9}{'k_var':>9}  {'dia_min':<12}{'idx':>4}{'ingresos':>10}")
    ks=[]
    for req in loader.sample_requests():
        prof=ds.profiles[req.user_id]
        exp=D(gt[req.request_id]["amount_safe_to_pay"]); tgt=exp+prof.minimum_balance_to_keep
        _,_,_,fl,ser,pts,lo,lo_day = curva(ds,req,facts,c)
        k1=fit(ds,req,facts,c,tgt)
        k2=fit(ds,req,facts,c,tgt,solo_variables=True)
        n_inc=sum(1 for f in fl if f.delta>0)
        idx=(lo_day-req.request_date).days
        capped = exp==req.requested_amount
        ks.append((req.request_id,k1,k2,idx,capped))
        print(f"{req.request_id:<11}{'':>4} {float(k1):>9.4f}{float(k2):>9.4f}  {str(lo_day):<12}{idx:>4}{n_inc:>10}"
              f"{'  (safe=requested: k es cota inferior)' if capped else ''}")
    libres=[k for _,k,_,_,cap in ks if not cap]
    libres.sort()
    print(f"\n  k_todo sobre los {len(libres)} NO capados: min={float(min(libres)):.3f} "
          f"mediana={float(libres[len(libres)//2]):.3f} max={float(max(libres)):.3f}")

main()
