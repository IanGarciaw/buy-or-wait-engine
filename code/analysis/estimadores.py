"""Historia cruda de cada serie + todos los estimadores, por usuario."""
from __future__ import annotations
import sys, os
from decimal import Decimal
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import loader
from finance import view as fv, series as fs, params
from extraction.facts import load_facts

def main(rids):
    ds=loader.load(); facts=load_facts(ds)
    reqs={r.request_id:r for r in loader.sample_requests()}
    for rid in rids:
        req=reqs[rid]; prof=ds.profiles[req.user_id]
        rec=fv.reconstruct(ds,req,facts)
        pts,lo,lo_day=fv.curve(rec)
        print("="*110); print(rid, req.user_id, "as_of",req.request_date,"min_obt",round(lo,2),"@",lo_day)
        # reconstruir la historia igual que reconstruct para poder listar montos
        for s in rec.series:
            hist=[]
            for e in ds.events_by_user[req.user_id]:
                day=e.settlement_date or e.event_date
                if day is None or day>req.request_date or e.status!="settled": continue
                k=(e.direction,e.event_type,e.category)
                if k!=s.key[:3]: continue
                if len(s.key)>3 and fs.norm(e.description)!=s.key[3]: continue
                a=e.amount
                if a is not None:
                    a=fv._home(abs(a),e,prof,day,ds)
                hist.append((day,a,e.event_id,e.description))
            hist.sort()
            amts=[a for _,a,_,_ in hist if a is not None]
            look=amts[-params.LOOKBACK:] if params.LOOKBACK else amts
            occ=len(fs.occurrences(s, rec.start, rec.end))
            print(f"\n  {s.direction}/{s.event_type}/{s.category} {s.period_kind}:{s.period} "
                  f"occ_hist={len(hist)} occ_fut={occ} varies={s.varies} usado={s.amount:.2f}")
            print(f"     historia: " + ", ".join(f"{d}:{'?' if a is None else format(a,'.2f')}" for d,a,_,_ in hist[-10:]))
            if look:
                print(f"     last6={[format(x,'.2f') for x in look]}")
                for how in ("mean","median","last","p75","max"):
                    print(f"       {how:<7}={fs.estimate(look,how):>14.2f}  x{occ} = {fs.estimate(look,how)*occ:>14.2f}")
            print(f"       min_hist={min(look) if look else 0:>14.2f}  x{occ} = {(min(look) if look else 0)*occ:>14.2f}")

main(sys.argv[1:])
