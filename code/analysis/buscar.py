"""Busqueda exhaustiva: que combinacion (estimador x conteo) reproduce el objetivo GT."""
from __future__ import annotations
import sys, os, itertools
from decimal import Decimal as D
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import loader
from analysis.lab import Cfg, data, curva, estimate, occurrences

def series_hist(ds, req, prof, s, c):
    """Montos historicos (en moneda del usuario) de la serie s."""
    from analysis.lab import norm, _home
    out=[]
    for e in ds.events_by_user[req.user_id]:
        day = e.settlement_date or e.event_date
        if day is None or day > req.request_date or e.status != "settled": continue
        if (e.direction, e.event_type, e.category) != s.key[:3]: continue
        if len(s.key) > 3 and norm(e.description) != s.key[3]: continue
        if e.amount is None: continue
        out.append((day, _home(abs(e.amount), e, prof, day, ds)))
    out.sort()
    return [a for _, a in out]

def main(rid, objetivo=None):
    c = Cfg(); ds, facts = data()
    gt = {r["request_id"]: r for r in loader.load_samples()}
    req = {r.request_id: r for r in loader.sample_requests()}[rid]
    prof, start, end, fl, ser, pts, lo, lo_day = curva(ds, req, facts, c)
    exp = D(gt[rid]["amount_safe_to_pay"]); tgt = exp + prof.minimum_balance_to_keep
    obj = objetivo if objetivo is not None else (prof.current_available_balance - tgt)
    print(f"{rid}: objetivo de SALIDA NETA 90d = {obj}")
    debit = [s for s in ser if s.direction == "debit"]
    ests = ("mean","median","last","p75","max","min")
    info=[]
    for s in debit:
        h = series_hist(ds, req, prof, s, c)
        n = len(occurrences(s, start, end, c))
        vals = {e: estimate(h, e, c.lookback) for e in ests}
        vals.update({e+"_all": estimate(h, e, 0) for e in ests})
        info.append((s, n, vals, h))
        print(f"  {s.cat:<18}{s.kind}:{s.period:<4} n={n}  " +
              " ".join(f"{k}={float(v):.2f}" for k,v in list(vals.items())[:6]))
    # barrido: un estimador comun, conteos n y n-1
    best=[]
    for est in list(ests)+[e+"_all" for e in ests]:
        base=[(s, n, vals[est]) for s,n,vals,_ in info]
        for mask in itertools.product(*[(n, max(0,n-1)) for _,n,_ in base]):
            tot=sum(a*D(k) for (s,_,a),k in zip(base,mask))
            best.append((abs(tot-obj), est, mask, tot))
    best.sort(key=lambda t: t[0])
    print("  --- mejores combinaciones (estimador comun, conteo n o n-1) ---")
    for diff, est, mask, tot in best[:6]:
        print(f"   diff={float(diff):>12.2f} est={est:<10} total={float(tot):>12.2f} "
              f"conteos=" + ",".join(f"{s.cat[:6]}:{k}" for (s,_,_),k in zip([(s,n,None) for s,n,_,_ in info], mask)))

if __name__ == "__main__":
    main(sys.argv[1], D(sys.argv[2]) if len(sys.argv)>2 else None)
