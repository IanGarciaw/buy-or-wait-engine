"""Marcador completo de A2 + la métrica que motiva el piso de 90 días.

    python3 -m decision.medir            # BEFORE/AFTER completo
    python3 -m decision.medir --guardar  # además congela el estado por fila en cache/

Mide lo que A0 pidió: status · safe ±1/5/10% · method · plan · earliest · filas exactas
· error mediano de safe · nº de planes que rompen el piso en las 250 · qué samples
mejoran, empeoran y no cambian.

El instrumento se calibra antes de creerle: `piso_por_curva` (lo que puede correr DENTRO
de decide, que sólo recibe la vista) se coteja contra `finance.view.simulate` (lo que usó
A0, que reconstruye el flujo entero). Si los dos no coinciden, el número de violaciones no
vale y se dice.
"""
from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal
from pathlib import Path

import loader
from contracts import fmt_amount, fmt_date, render_changes, render_plan

from . import decide
from .safety import worst_balance

CACHE = Path(__file__).resolve().parent.parent / "cache"
COLS = ("amount_safe_to_pay", "affordability_status", "recommended_payment_method",
        "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed")


def _fila(d) -> dict:
    return {
        "amount_safe_to_pay": fmt_amount(d.amount_safe_to_pay),
        "affordability_status": d.affordability_status,
        "recommended_payment_method": d.recommended_payment_method,
        "payment_plan": render_plan(d.payments),
        "earliest_date_for_full_payment": fmt_date(d.earliest_date_for_full_payment),
        "spending_changes_needed": render_changes(d.spending_changes),
    }


def _rel(a: str, b: str) -> Decimal | None:
    try:
        x, y = Decimal(a), Decimal(b)
    except Exception:
        return None
    if y == 0:
        return Decimal(0) if x == 0 else Decimal(1)
    return abs(x - y) / abs(y)


def _creditos(d, view, req):
    """Ahorro de los cambios recomendados, en las fechas en que dejan de cobrarse."""
    from .changes import opciones
    por_evento = {o.event_id: o for o in opciones(view, req)}
    out = []
    for c in d.spending_changes:
        o = por_evento.get(c.event_id)
        if o is not None:
            out.extend(o.credits)
    return tuple(out)


def corre_samples(etiqueta: str) -> dict:
    from finance import build_view
    ds = loader.load()
    gts = {g["request_id"]: g for g in loader.load_samples()}
    filas, viol = {}, 0
    for req in loader.sample_requests():
        v = build_view(ds, req, {})
        d = decide(v, req, ds.options_by_request.get(req.request_id, ()))
        filas[req.request_id] = _fila(d)
        if d.payments and worst_balance(v, d.payments, _creditos(d, v, req)) < \
                v.profile.minimum_balance_to_keep:
            viol += 1

    tot = {c: 0 for c in COLS}
    tot.update({"exactas": 0, "safe1": 0, "safe5": 0, "safe10": 0})
    errores = []
    for rid, f in filas.items():
        gt = gts[rid]
        for c in COLS:
            if f[c] == gt[c]:
                tot[c] += 1
        if all(f[c] == gt[c] for c in COLS):
            tot["exactas"] += 1
        e = _rel(f["amount_safe_to_pay"], gt["amount_safe_to_pay"])
        if e is not None:
            errores.append(e)
            for k, lim in (("safe1", "0.01"), ("safe5", "0.05"), ("safe10", "0.10")):
                if e <= Decimal(lim):
                    tot[k] += 1
    tot["err_mediano"] = float(statistics.median(errores)) if errores else None
    tot["viola_piso_samples"] = viol
    return {"etiqueta": etiqueta, "filas": filas, "tot": tot}


def viola_piso_250(calibrar_contra_a1: bool = False) -> dict:
    from finance import build_view
    ds = loader.load()
    rotos, planes, desacuerdo, brechas = 0, 0, 0, []
    sim = None
    if calibrar_contra_a1:
        from finance.view import simulate as sim
    for req in ds.requests:
        v = build_view(ds, req, {})
        d = decide(v, req, ds.options_by_request.get(req.request_id, ()))
        if not d.payments:
            continue
        planes += 1
        cred = _creditos(d, v, req)
        mio = worst_balance(v, d.payments, cred)
        minimo = v.profile.minimum_balance_to_keep
        if mio < minimo:
            rotos += 1
            brechas.append((float(minimo - mio), req.request_id,
                            d.recommended_payment_method))
        if sim is not None:
            suyo = sim(ds, req, {},
                       tuple(c.event_id for c in d.spending_changes if c.kind == "stop"),
                       tuple((c.event_id, c.new_amount) for c in d.spending_changes
                             if c.kind == "reduce_to"),
                       tuple((p.day, p.amount) for p in d.payments))
            if (mio < minimo) != (suyo < minimo):
                desacuerdo += 1
    brechas.sort(reverse=True)
    return {"planes": planes, "rotos": rotos, "peores": brechas[:4],
            "desacuerdo_con_a1": desacuerdo if sim is not None else None}


def informe(etiqueta: str, guardar: bool = False, calibrar: bool = False) -> dict:
    r = corre_samples(etiqueta)
    t = r["tot"]
    p = viola_piso_250(calibrar)
    print(f"\n=== {etiqueta} ===")
    print(f"  status {t['affordability_status']}/25 · method "
          f"{t['recommended_payment_method']}/25 · plan {t['payment_plan']}/25 · "
          f"earliest {t['earliest_date_for_full_payment']}/25 · "
          f"cambios {t['spending_changes_needed']}/25 · FILAS EXACTAS {t['exactas']}/25")
    print(f"  safe ±1% {t['safe1']}/25 · ±5% {t['safe5']}/25 · ±10% {t['safe10']}/25 · "
          f"error mediano {t['err_mediano']:.4f}" if t["err_mediano"] is not None else "")
    print(f"  planes que ROMPEN el piso: {p['rotos']}/{p['planes']} en las 250 · "
          f"{t['viola_piso_samples']}/14 en los samples")
    if p["peores"]:
        print("  peores: " + " · ".join(f"{rid} falta {g:,.2f} ({m})"
                                        for g, rid, m in p["peores"]))
    if p["desacuerdo_con_a1"] is not None:
        print(f"  calibración contra finance.view.simulate: "
              f"{p['desacuerdo_con_a1']} desacuerdos de {p['planes']} planes")
    r["piso"] = p
    if guardar:
        CACHE.mkdir(exist_ok=True)
        (CACHE / f"a2_{etiqueta}.json").write_text(
            json.dumps({"filas": r["filas"], "tot": {k: (str(v) if isinstance(v, Decimal)
                                                         else v)
                                                     for k, v in t.items()},
                        "piso": p}, indent=1, default=str))
    return r


def comparar(antes: dict, despues: dict) -> None:
    gts = {g["request_id"]: g for g in loader.load_samples()}
    mejor, peor, igual = [], [], 0
    for rid in sorted(antes["filas"]):
        a, b = antes["filas"][rid], despues["filas"][rid]
        if a == b:
            igual += 1
            continue
        na = sum(1 for c in COLS if a[c] == gts[rid][c])
        nb = sum(1 for c in COLS if b[c] == gts[rid][c])
        (mejor if nb > na else peor if nb < na else mejor).append(f"{rid}({na}->{nb})")
    print(f"\n  MEJORAN: {', '.join(mejor) or 'ninguno'}")
    print(f"  EMPEORAN: {', '.join(peor) or 'ninguno'}")
    print(f"  SIN CAMBIO: {igual}/25")


if __name__ == "__main__":
    informe(sys.argv[1] if len(sys.argv) > 1 else "actual",
            "--guardar" in sys.argv, "--calibrar" in sys.argv)
