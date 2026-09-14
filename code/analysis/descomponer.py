"""Descomposicion del horizonte: cuanto aporta cada serie, y que implica la verdad."""
from __future__ import annotations
import sys, os
from decimal import Decimal as D
from collections import defaultdict
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import loader
from analysis.lab import Cfg, data, curva, safe_de

def main(rids, cfg=None):
    c = cfg or Cfg()
    ds, facts = data()
    gt = {r["request_id"]: r for r in loader.load_samples()}
    reqs = {r.request_id: r for r in loader.sample_requests()}
    for rid in rids:
        req = reqs[rid]
        prof, start, end, fl, ser, pts, lo, lo_day = curva(ds, req, facts, c)
        exp = D(gt[rid]["amount_safe_to_pay"]); tgt = exp + prof.minimum_balance_to_keep
        print("="*104)
        print(f"{rid} {req.user_id} {prof.home_currency}  as_of={start} end={end}  bal={prof.current_available_balance} "
              f"min_keep={prof.minimum_balance_to_keep} requested={req.requested_amount}")
        print(f"  GT safe={exp}  -> min_90d OBJETIVO = {tgt}   |  NUESTRO min={lo:.2f} @ {lo_day}  "
              f"DELTA={lo-tgt:.2f}")
        print(f"  GT earliest={gt[rid]['earliest_date_for_full_payment'] or '(vacio)'}  GT status={gt[rid]['affordability_status']}")
        # aporte por serie hasta el dia del minimo y en todo el horizonte
        agg_h = defaultdict(lambda: [0, D(0)]); agg_m = defaultdict(lambda: [0, D(0)])
        for f in fl:
            k = f.note if f.note.startswith(("scheduled","pending","nuevo")) else f"{f.note}|{f.eid}"
            agg_h[k][0] += 1; agg_h[k][1] += f.delta
            if f.day <= lo_day:
                agg_m[k][0] += 1; agg_m[k][1] += f.delta
        print(f"  {'concepto':<46}{'n_hasta_min':>12}{'monto_hasta_min':>18}{'n_90d':>7}{'monto_90d':>18}")
        for k in sorted(agg_h, key=lambda k: agg_h[k][1]):
            nh, mh = agg_m.get(k, [0, D(0)]); n9, m9 = agg_h[k]
            print(f"  {k[:46]:<46}{nh:>12}{mh:>18.2f}{n9:>7}{m9:>18.2f}")
        tot_in = sum(f.delta for f in fl if f.delta > 0)
        tot_out = sum(f.delta for f in fl if f.delta < 0)
        print(f"  {'TOTAL 90d':<46}{'':>12}{'':>18}{'':>7}  in={tot_in:.2f} out={tot_out:.2f} neto={tot_in+tot_out:.2f}")
        print(f"  GT implica neto_90d(hasta su minimo) = {tgt - prof.current_available_balance:.2f}")

if __name__ == "__main__":
    main(sys.argv[1:])
