import sys, importlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
import loader
from finance import params
from finance.view import build_view
from decision.engine import decide
from decision.safety import worst_balance

ds = loader.load()
gt = {r["request_id"]: r for r in loader.load_samples()}
SM = loader.sample_requests()

print("### 1) planes que rompen el piso SIN cambios de gasto (violacion inequivoca) ###")
for reqs, lbl in ((SM,"25 samples"), (ds.requests,"250 requests")):
    n=0; tot=0
    for req in reqs:
        v = build_view(ds, req, {})
        dec = decide(v, req, ds.options_by_request.get(req.request_id, ()))
        if not dec.payments: continue
        tot+=1
        if dec.spending_changes: continue
        if worst_balance(v, dec.payments) < v.profile.minimum_balance_to_keep: n+=1
    print(f"  {lbl}: {n} de {tot} planes recomendados rompen el piso y NO traen cambios de gasto")

def marcador(lbl):
    d1=d5=0
    for req in SM:
        v = build_view(ds, req, {})
        esp = Decimal(gt[req.request_id]["amount_safe_to_pay"] or 0)
        err = abs(v.amount_safe_today-esp)/esp if esp else (Decimal(0) if v.amount_safe_today==0 else Decimal(1))
        if err<=Decimal("0.01"): d1+=1
        if err<=Decimal("0.05"): d5+=1
    print(f"  {lbl:<42} ±1%={d1:2d}/25  ±5%={d5:2d}/25")

print("\n### 2) fronteras de fecha: sensibilidad medida ###")
marcador("HORIZON=90, INCLUDE_DAY_ZERO=True (actual)")
params.INCLUDE_DAY_ZERO = False
marcador("INCLUDE_DAY_ZERO=False (dia 0 excluido)")
params.INCLUDE_DAY_ZERO = True
params.HORIZON = 89
marcador("HORIZON=89 (extremo derecho exclusivo)")
params.HORIZON = 91
marcador("HORIZON=91")
params.HORIZON = 90

print("\n### 3) inventario de constantes (HEURISTIC?) ###")
import re
src = (CODE/"finance"/"params.py").read_text()
for m in re.finditer(r"^([A-Z_]+(?:,\s*[A-Z_]+)*)\s*=\s*(.+)$", src, re.M):
    print(f"  params.{m.group(1)} = {m.group(2)}")
for f,pat in (("finance/series.py", r"^\s*(?:MAX|MIN|_?[A-Z_]{3,})\s*=\s*.+$"),):
    pass
print("  decision/changes.py MAX_CHANGES = 3")
print("  decision/candidates.py meses_del_plan: 27<=f<=32 -> mensual ; Decimal('30.44')")
print("  decision/series.py _cadence: 27<=med<=32 -> mensual ; montos = media de las ULTIMAS 3")
print("  decision/series.py guard k<=400 ; finance/series.py guard 200/400")
print("  finance/view.py _dedupe win = 15 dias (mensual) o period//2")
print("  finance/view.py _dias_ingreso: (nueva-d).days > 25")
print("  finance/view.py _mensual / _dias_ingreso: k < 12 repeticiones")
print("  decision/ranking.py _id_num sin opcion -> 10**9")
