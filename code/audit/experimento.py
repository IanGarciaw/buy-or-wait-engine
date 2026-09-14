"""Falsificadores: se cambia UNA regla y se mide `safe` contra la verdad de campo.
No modifica nada fuera de code/audit/: muta el Dataset ya cargado en memoria."""
import sys, csv
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
import loader
from contracts import Dataset
from finance.view import build_view

ds = loader.load()
reqs = loader.sample_requests()
gt = {r["request_id"]: r for r in loader.load_samples()}

def variante(nombre, filtro):
    ebu = {}
    tocados = 0
    for u, evs in ds.events_by_user.items():
        keep = []
        for e in evs:
            r = filtro(e)
            if r is None: tocados += 1; continue
            keep.append(r)
        ebu[u] = tuple(keep)
    d2 = Dataset(profiles=ds.profiles, events_by_user=ebu, requests=ds.requests,
                 options_by_request=ds.options_by_request,
                 messages_by_user=ds.messages_by_user, images=ds.images, rates=ds.rates)
    filas = []
    for req in reqs:
        v = build_view(d2, req, {})
        esp = Decimal(gt[req.request_id]["amount_safe_to_pay"] or 0)
        obt = v.amount_safe_today
        err = abs(obt - esp) / esp if esp else (Decimal(0) if obt == 0 else Decimal(1))
        filas.append((req.request_id, esp, obt, err))
    d1 = sum(1 for _,_,_,e in filas if e <= Decimal("0.01"))
    d5 = sum(1 for _,_,_,e in filas if e <= Decimal("0.05"))
    print(f"{nombre:<44} ±1%={d1:2d}/25  ±5%={d5:2d}/25   (filas alteradas: {tocados})")
    return {f[0]: f for f in filas}

base = variante("A) BASELINE (código tal cual)", lambda e: e)
sinpd = variante("B) pending DEBIT no se reserva (se borra)",
                 lambda e: None if (e.status=="pending" and e.direction=="debit") else e)
conpc = variante("C) pending CREDIT contado (simétrico: -> scheduled)",
                 lambda e: replace(e, status="scheduled") if (e.status=="pending" and e.direction=="credit") else e)
sindup = variante("D) 'Possible duplicate card charge' ignorado",
                  lambda e: None if e.description=="Possible duplicate card charge" else e)
sched_cre = variante("E) scheduled CREDIT no contado",
                  lambda e: None if (e.status=="scheduled" and e.direction=="credit") else e)
sched_deb = variante("F) scheduled DEBIT no contado",
                  lambda e: None if (e.status=="scheduled" and e.direction=="debit") else e)
canc = variante("G) cancelled/failed CONTADOS (-> settled)",
                  lambda e: replace(e, status="settled") if e.status in ("cancelled","failed") else e)
unreal = variante("H) unrealized/non_cash CONTADOS (-> settled/credit)",
                  lambda e: replace(e, status="settled", direction="credit") if e.status=="unrealized" else e)

print("\n--- por request, donde B cambia respecto de A (pending debit) ---")
for rid in base:
    if base[rid][2] != sinpd[rid][2]:
        print(f"  {rid}: gt={base[rid][1]}  A={base[rid][2]} (err {base[rid][3]:.2%})  B={sinpd[rid][2]} (err {sinpd[rid][3]:.2%})")
print("\n--- por request, donde C cambia respecto de A (pending credit) ---")
for rid in base:
    if base[rid][2] != conpc[rid][2]:
        print(f"  {rid}: gt={base[rid][1]}  A={base[rid][2]} (err {base[rid][3]:.2%})  C={conpc[rid][2]} (err {conpc[rid][3]:.2%})")
print("\n--- D (duplicados) ---")
print("  samples alterados:", [rid for rid in base if base[rid][2]!=sindup[rid][2]] or "NINGUNO")
