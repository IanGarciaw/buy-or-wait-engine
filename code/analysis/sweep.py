"""Barrido de hipotesis sobre el lab. Cada fila = una hipotesis, con ayuda/perjudica/igual."""
from __future__ import annotations
import sys, os
from decimal import Decimal as D
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from analysis.lab import Cfg, run_all, hit
from dataclasses import replace as dcreplace

def relerr(r):
    e,o = r["exp"], r["obt"]
    if e == 0: return D(0) if o == 0 else D(1)
    return abs(e-o)/abs(e)

def score(res):
    h1 = sum(1 for r in res if hit(r,"0.01"))
    h5 = sum(1 for r in res if hit(r,"0.05"))
    errs = sorted(relerr(r) for r in res)
    med = errs[len(errs)//2]
    ne = sum(1 for r in res if r["earl_exp"]==r["earl_obt"])
    return h1,h5,med,ne

def compare(base, new):
    mejor=peor=igual=0; det=[]
    for b,n in zip(base,new):
        rb, rn = relerr(b), relerr(n)
        if rn < rb - D("0.0005"): mejor+=1; det.append(("+",b["rid"],float(rb),float(rn)))
        elif rn > rb + D("0.0005"): peor+=1; det.append(("-",b["rid"],float(rb),float(rn)))
        else: igual+=1
    return mejor,peor,igual,det

BASE = Cfg()
base = run_all(BASE)
bh1,bh5,bmed,bne = score(base)
print(f"BASE                                   ±1%={bh1:>2} ±5%={bh5:>2} medERR={float(bmed)*100:6.2f}% earl={bne}")

HIPS = [
 ("drop_first_monthly",           dict(drop_first_monthly=True)),
 ("max_future_monthly=2",         dict(max_future_monthly=2)),
 ("horizon=89",                   dict(horizon=89)),
 ("horizon=87",                   dict(horizon=87)),
 ("horizon=85",                   dict(horizon=85)),
 ("include_day_zero=False",       dict(include_day_zero=False)),
 ("estimator=median",             dict(estimator="median")),
 ("estimator=last",               dict(estimator="last")),
 ("estimator=min",                dict(estimator="min")),
 ("lookback=0(todo)",             dict(lookback=0)),
 ("lookback=12",                  dict(lookback=12)),
 ("var_mult=0.9",                 dict(var_mult="0.9")),
 ("not_yet_cash=ignorar",         dict(not_yet_cash_mode="ignorar")),
 ("new_recurring=gasto",          dict(new_recurring_mode="gasto")),
 ("new_recurring=ignorar",        dict(new_recurring_mode="ignorar")),
 ("income_terminal=False",        dict(income_terminal=False)),
 ("sin hechos",                   dict(use_facts=False)),
 ("max_future_weekly=12",         dict(max_future_weekly=12)),
]
for name, kw in HIPS:
    c = dcreplace(BASE, **kw)
    res = run_all(c)
    h1,h5,med,ne = score(res)
    mej,peo,ig,det = compare(base,res)
    print(f"{name:<34} ±1%={h1:>2} ±5%={h5:>2} medERR={float(med)*100:6.2f}% earl={ne}  "
          f"mejora={mej} empeora={peo} igual={ig}")
    if len(sys.argv)>1:
        for s,rid,a,b in det: print(f"      {s} {rid} {a*100:.1f}% -> {b*100:.1f}%")
