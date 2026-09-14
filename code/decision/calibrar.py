"""KPI del motor de decisión contra los 25 samples resueltos.

    python3 -m decision.calibrar            # los dos modos
    python3 -m decision.calibrar --detalle  # además, el diff fila por fila

Dos modos, porque medir el motor de decisión con un forecast malo no mide el motor:

  A1       — la vista real de finance.build_view. Es el número integrado: mide A1+A2.
  ORÁCULO  — `amount_safe_today` y `earliest_full_payment` se toman del ground truth y la
             curva del andamio se DESPLAZA para que su mínimo case con esa capacidad.
             Aísla A2: lo que falle aquí es culpa del motor de decisión.
  ANDAMIO  — todo sale de decision/stub_forecast.py (que NO es A1). Sirve para ver el
             extremo a extremo y para medir cuánto le falta al forecast.

El instrumento se calibra antes de creerle: el informe imprime también cuántos
`amount_safe_to_pay` y cuántas fechas reproduce el andamio por su cuenta.

Los samples son usuarios DISJUNTOS de la evaluación: sirven para descubrir la regla,
nunca para memorizar la respuesta. Aquí no hay ni un request_id.
"""
from __future__ import annotations

import sys
from decimal import Decimal

import loader
from contracts import (CashPoint, Decision, FinanceView, Forecast, fmt_amount,
                       fmt_date, render_changes, render_plan)

from . import decide


def _plan_a2(d: Decision) -> str:
    return render_plan(d.payments)


def _cambios_a2(d: Decision) -> str:
    return render_changes(d.spending_changes)


def _oraculo(ds, req, gt) -> FinanceView:
    """Vista con la CAPACIDAD del ground truth y la forma de curva del andamio."""
    from .stub_forecast import build_view
    v = build_view(ds, req)
    m = v.profile.minimum_balance_to_keep
    safe = Decimal(gt["amount_safe_to_pay"] or "0")
    earliest = loader._d(gt["earliest_date_for_full_payment"])
    piso = m + (safe if safe < req.requested_amount else req.requested_amount)
    delta = piso - v.forecast.min_balance
    if safe >= req.requested_amount and delta < 0:
        delta = Decimal(0)                      # capado: sólo se sube la curva, nunca se baja
    pts = tuple(CashPoint(p.day, p.balance + delta, p.note) for p in v.forecast.points)
    fc = Forecast(v.forecast.user_id, v.forecast.start, pts,
                  v.forecast.min_balance + delta, v.forecast.min_balance_day)
    return FinanceView(v.profile, fc, safe, earliest, v.flexible_events, 1.0,
                       ("oraculo",))


def correr(modo: str, detalle: bool = False) -> dict:
    ds = loader.load()
    gts = {g["request_id"]: g for g in loader.load_samples()}
    reqs = loader.sample_requests()
    from .stub_forecast import build_view
    a1 = None
    if modo == "a1":
        try:
            from finance import build_view as a1
        except Exception as e:            # A1 todavía no está: se dice, no se finge
            print(f"\n=== MODO A1 — no disponible ({e}) ===")
            return {}

    tot = {k: 0 for k in ("status", "method", "plan", "safe", "earliest", "cambios")}
    filas = []
    for req in reqs:
        gt = gts[req.request_id]
        if modo == "oraculo":
            view = _oraculo(ds, req, gt)
        elif modo == "a1":
            view = a1(ds, req, {})
        else:
            view = build_view(ds, req)
        d = decide(view, req, ds.options_by_request.get(req.request_id, ()))

        ok = {
            "status": d.affordability_status == gt["affordability_status"],
            "method": d.recommended_payment_method == gt["recommended_payment_method"],
            "plan": _plan_a2(d) == gt["payment_plan"],
            "safe": fmt_amount(d.amount_safe_to_pay) == gt["amount_safe_to_pay"],
            "earliest": fmt_date(d.earliest_date_for_full_payment) == gt[
                "earliest_date_for_full_payment"],
            "cambios": _cambios_a2(d) == gt["spending_changes_needed"],
        }
        for k, v in ok.items():
            tot[k] += bool(v)
        filas.append((req.request_id, ok, d, gt))

    print(f"\n=== MODO {modo.upper()} — {len(reqs)} samples ===")
    for k in ("status", "method", "plan", "safe", "earliest", "cambios"):
        print(f"  {k:<9} {tot[k]:>2}/{len(reqs)}")

    if detalle:
        for rid, ok, d, gt in filas:
            malos = [k for k, v in ok.items() if not v]
            if not malos:
                continue
            print(f"\n  {rid}  falla: {','.join(malos)}")
            print(f"    esperado: {gt['affordability_status']} / "
                  f"{gt['recommended_payment_method']} / {gt['payment_plan']} / "
                  f"safe={gt['amount_safe_to_pay']} / "
                  f"earliest={gt['earliest_date_for_full_payment']!r} / "
                  f"cambios={gt['spending_changes_needed']}")
            print(f"    obtenido: {d.affordability_status} / "
                  f"{d.recommended_payment_method} / {_plan_a2(d)} / "
                  f"safe={fmt_amount(d.amount_safe_to_pay)} / "
                  f"earliest={fmt_date(d.earliest_date_for_full_payment)!r} / "
                  f"cambios={_cambios_a2(d)}")
            print(f"    traza: {d.trace[:300]}")
    return tot


if __name__ == "__main__":
    det = "--detalle" in sys.argv
    modos = [m for m in ("a1", "oraculo", "andamio") if f"--{m}" in sys.argv] \
        or ["a1", "oraculo", "andamio"]
    for m in modos:
        correr(m, det)
