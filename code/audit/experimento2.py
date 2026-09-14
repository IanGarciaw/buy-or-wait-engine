import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
import loader
from finance.view import build_view, reconstruct
from decision.engine import decide
from decision.safety import worst_balance

ds = loader.load()
def corre(reqs, lbl):
    viol = []; boundary0 = 0; boundary90 = 0; flujos = 0
    safe_eq_head = 0
    for req in reqs:
        v = build_view(ds, req, {})
        rec = reconstruct(ds, req, {})
        end = req.request_date + timedelta(days=90)
        for f in rec.flows:
            flujos += 1
            if f.day == req.request_date: boundary0 += 1
            if f.day == end: boundary90 += 1
        opts = ds.options_by_request.get(req.request_id, ())
        dec = decide(v, req, opts)
        if dec.payments:
            w = worst_balance(v, dec.payments)
            if w < v.profile.minimum_balance_to_keep:
                viol.append((req.request_id, dec.recommended_payment_method,
                             w, v.profile.minimum_balance_to_keep,
                             v.profile.minimum_balance_to_keep - w))
    print(f"\n== {lbl}: {len(reqs)} requests, {flujos} flujos ==")
    print(f"  flujos EXACTAMENTE en request_date (dia 0): {boundary0}")
    print(f"  flujos EXACTAMENTE en request_date+90 (dia 90): {boundary90}")
    print(f"  planes recomendados que ROMPEN el piso de 90 dias: {len(viol)} / {len(reqs)}")
    for r in viol[:12]:
        print(f"    {r[0]} {r[1]}: min curva {r[2]:.2f} < minimo {r[3]:.2f} (faltan {r[4]:.2f})")
    if len(viol) > 12: print(f"    ... y {len(viol)-12} mas")
    return viol

corre(loader.sample_requests(), "25 SAMPLES")
corre(ds.requests, "250 REQUESTS")
