#!/usr/bin/env python3
"""Buy or Wait? — punto de entrada.

    python3 code/main.py              # 250 requests -> output.csv en la raíz
    python3 code/main.py --samples    # los 25 samples -> marcador contra la verdad
    python3 code/main.py --no-facts   # sin llamadas al modelo (sólo CSV)

Orquesta: cargar -> hechos (imágenes+mensajes) -> reconstruir -> forecast -> decidir
-> verificar -> escribir. Cada componente vive en su carpeta y se integra sólo por
los contratos de contracts.py.

Si un componente todavía no existe, entra un RESPALDO interno para que el camino
completo siempre produzca 250 filas. Un envío incompleto vale cero.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from contracts import (BLOCK, HORIZON_DAYS, OUTPUT_COLUMNS, PASS, REPAIR,  # noqa: E402
                       CashPoint, Dataset, Decision, FinanceView, Forecast,
                       Payment, Request, Verdict, to_row)
import loader  # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "output.csv"


# ─────────────────────────────────────────────────────────────────────────────
# Enganche de componentes — cada uno opcional, con respaldo
# ─────────────────────────────────────────────────────────────────────────────

def _try(module: str, attr: str):
    try:
        mod = __import__(module, fromlist=[attr])
        return getattr(mod, attr)
    except Exception:
        return None


build_view = _try("finance.view", "build_view") or _try("finance.forecast", "build_view")
decide = _try("decision.engine", "decide") or _try("decision.decide", "decide")
load_facts = _try("extraction.facts", "load_facts")
verify = _try("evaluation.verifier", "verify")
score = _try("evaluation.scorer", "score")


# ─────────────────────────────────────────────────────────────────────────────
# RESPALDOS — mínimos, deterministas, sólo para que el camino nunca se corte
# ─────────────────────────────────────────────────────────────────────────────

def _fallback_view(ds: Dataset, req: Request, facts: dict) -> FinanceView:
    """Forecast de respaldo: saldo + eventos futuros confirmados, sin recurrencia.

    Es deliberadamente simple. Existe para garantizar 250 filas, no para puntuar.
    """
    p = ds.profiles[req.user_id]
    bal = p.current_available_balance
    start = req.request_date
    end = start + timedelta(days=HORIZON_DAYS)

    movimientos: list[tuple] = []
    for e in ds.events_by_user.get(req.user_id, ()):
        when = e.settlement_date or e.event_date
        if not when or when < start or when > end:
            continue
        if e.status in ("cancelled", "failed", "unrealized"):
            continue
        if e.direction == "non_cash":
            continue
        if e.status == "pending" and e.direction == "credit":
            continue                      # crédito pendiente no es efectivo
        amt = e.amount
        if amt is None:
            fact = (facts.get("images") or {}).get(e.event_id)
            amt = fact.amount if fact and fact.amount is not None else None
        if amt is None:
            continue
        amt = loader.convert(amt, e.currency, p.home_currency, when, ds.rates)
        movimientos.append((when, amt if e.direction == "credit" else -amt))

    movimientos.sort(key=lambda m: m[0])
    pts = [CashPoint(start, bal)]
    run = bal
    for when, delta in movimientos:
        run += delta
        pts.append(CashPoint(when, run))
    lo = min(pts, key=lambda c: c.balance)

    fc = Forecast(user_id=req.user_id, start=start, points=tuple(pts),
                  min_balance=lo.balance, min_balance_day=lo.day)
    head = fc.headroom(p.minimum_balance_to_keep)
    safe = min(req.requested_amount, head)

    earliest = None
    if safe >= req.requested_amount:
        earliest = start
    else:
        run = bal
        for when, delta in movimientos:
            run += delta
            if when < start:
                continue
            resto = [c.balance for c in pts if c.day >= when]
            if resto and (min(resto) - req.requested_amount) >= p.minimum_balance_to_keep:
                earliest = when
                break

    flex = tuple(e for e in ds.events_by_user.get(req.user_id, ())
                 if e.flexibility in ("reducible", "stoppable", "reducible_or_stoppable"))
    return FinanceView(profile=p, forecast=fc, amount_safe_today=max(Decimal(0), safe),
                       earliest_full_payment=earliest, flexible_events=flex,
                       confidence=0.3, notes=("respaldo: sin recurrencia",))


def _fallback_decide(view: FinanceView, req: Request, options) -> Decision:
    """Decisión de respaldo: sólo full_payment / wait / not_recommended."""
    p = view.profile
    safe = min(view.amount_safe_today, req.requested_amount)
    acepta_full = "full_payment" in p.methods_considered
    completo_hoy = safe >= req.requested_amount

    if completo_hoy and acepta_full:
        return Decision(req.request_id, safe, "affordable_now", "full_payment",
                        (Payment(req.request_date, req.requested_amount),),
                        req.request_date, (), confidence=0.3)

    ef = view.earliest_full_payment
    if ef and acepta_full and ef <= req.desired_completion_date:
        return Decision(req.request_id, safe, "affordable_later", "wait",
                        (Payment(ef, req.requested_amount),), ef, (), confidence=0.3)

    return Decision(req.request_id, safe, "not_affordable", "not_recommended",
                    (), ef, (), confidence=0.3)


def _fallback_verify(d: Decision, req: Request, ds: Dataset) -> Verdict:
    """Sólo las invariantes que hacen INVÁLIDO el envío. Repara lo mecánico."""
    inc = []
    if d.amount_safe_to_pay < 0:
        d.amount_safe_to_pay = Decimal(0); inc.append("safe<0 reparado")
    if d.amount_safe_to_pay > req.requested_amount:
        d.amount_safe_to_pay = req.requested_amount; inc.append("safe>requested reparado")
    if d.affordability_status == "affordable_now" and \
            d.earliest_date_for_full_payment != req.request_date:
        d.earliest_date_for_full_payment = req.request_date
        inc.append("affordable_now: earliest alineado a request_date")
    return Verdict(REPAIR if inc else PASS, d, tuple(inc))


# ─────────────────────────────────────────────────────────────────────────────

def run(requests, ds: Dataset, facts: dict, quiet=False):
    fn_view = build_view or _fallback_view
    fn_decide = decide or _fallback_decide
    fn_verify = verify or _fallback_verify

    decisions, counts, incidents = [], {PASS: 0, REPAIR: 0, BLOCK: 0}, []
    for req in requests:
        try:
            view = fn_view(ds, req, facts)
            d = fn_decide(view, req, ds.options_by_request.get(req.request_id, ()))
        except Exception as exc:                       # ningún caso se pierde
            incidents.append(f"{req.request_id}: motor falló ({type(exc).__name__}: {exc})")
            d = Decision(req.request_id, Decimal(0), "not_affordable",
                         "not_recommended", (), None, (), confidence=0.0)
        try:
            # Los hechos tipados van al verificador para enriquecer sus avisos.
            # Es seguro desde que separó la autoridad por CLASE de invariante: lo
            # estructural (formato, rangos, opción real, flexibilidad) se comprueba
            # contra el dataset y puede BLOQUEAR; lo derivado de su propio forecast
            # (piso, nivel de safe, earliest) sólo puede ADVERTIR. Un primer intento
            # de dejarle vetar el nivel costó -3 status y -3 method: está medido.
            try:
                v = fn_verify(d, req, ds, facts)
            except TypeError:
                v = fn_verify(d, req, ds)
            counts[v.outcome] = counts.get(v.outcome, 0) + 1
            if v.incidents:
                incidents.extend(f"{req.request_id}: {i}" for i in v.incidents)
            d = v.decision
        except Exception as exc:
            incidents.append(f"{req.request_id}: verificador falló ({exc})")
        if not d.decision_explanation:
            d.decision_explanation = _explain(d, req)
        decisions.append(d)

    if not quiet:
        print(f"verificador -> PASS {counts[PASS]} · REPAIR {counts[REPAIR]} "
              f"· BLOCK {counts[BLOCK]}")
        for i in incidents[:10]:
            print("  !", i)
        if len(incidents) > 10:
            print(f"  ... y {len(incidents)-10} más")
    return decisions, counts, incidents


def _explain(d: Decision, req: Request) -> str:
    """Explicación de respaldo. Cifras congeladas: aquí sólo se redactan."""
    from contracts import fmt_amount
    a = fmt_amount(d.amount_safe_to_pay)
    if d.recommended_payment_method == "full_payment":
        return (f"Pay {fmt_amount(req.requested_amount)} today. "
                f"The forecast stays above the minimum balance for the next 90 days.")
    if d.recommended_payment_method == "wait":
        return (f"Only {a} is safe today. Waiting until "
                f"{d.earliest_date_for_full_payment} keeps the minimum balance protected.")
    if d.recommended_payment_method == "not_recommended":
        return (f"Do not proceed. Only {a} is safe today and no available option "
                f"keeps the minimum balance protected within the forecast.")
    if d.recommended_payment_method == "partial_payment":
        return (f"Pay {a} today and the remainder on "
                f"{d.earliest_date_for_full_payment}, keeping the minimum protected.")
    return f"Use the instalment plan shown; {a} is safe to pay today."


def write_csv(decisions, path: Path = OUT):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(OUTPUT_COLUMNS))
        w.writeheader()
        for d in decisions:
            w.writerow(to_row(d))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="correr los 25 y puntuar")
    ap.add_argument("--no-facts", action="store_true", help="sin llamadas al modelo")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ds = loader.load()
    facts = {"images": {}, "messages": {}}
    if load_facts and not args.no_facts:
        try:
            facts = load_facts(ds)
            print(f"hechos: {len(facts.get('images', {}))} imágenes · "
                  f"{sum(len(v) for v in facts.get('messages', {}).values())} mensajes")
        except Exception as exc:
            print(f"AVISO: extracción no disponible ({exc}); se sigue sin hechos")

    if args.samples:
        reqs = loader.sample_requests()
        decisions, counts, _ = run(reqs, ds, facts)
        if score:
            score(decisions)
        else:
            print("scorer todavía no disponible")
        return

    decisions, counts, _ = run(ds.requests, ds, facts)
    path = write_csv(decisions, Path(args.out) if args.out else OUT)
    print(f"\n{len(decisions)} filas -> {path}")
    faltan = len(ds.requests) - len(decisions)
    if faltan:
        print(f"ERROR: faltan {faltan} filas")
        sys.exit(1)


if __name__ == "__main__":
    main()
