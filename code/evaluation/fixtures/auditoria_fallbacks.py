#!/usr/bin/env python3
"""AUDITORÍA DE FALLBACKS — quién queda bajo el mínimo y por qué no había otra cosa.

    python3 code/evaluation/fixtures/auditoria_fallbacks.py            # tabla a stdout
    python3 code/evaluation/fixtures/auditoria_fallbacks.py --md       # markdown

NO cambia ninguna decisión. Corre el motor congelado sobre las 250 solicitudes, mide el
saldo mínimo del horizonte CON el plan recomendado encima usando el instrumento de A1
(`finance.view.simulate`, que reconstruye la curva desde los flujos), y para cada
violación enumera qué alternativas tenía el usuario y por qué ninguna limpia era elegible.

Dos instrumentos, a propósito:
  · `decision.safety.worst_balance` — el que usa la compuerta PISO (curva de A1 + pagos)
  · `finance.view.simulate`        — reconstrucción independiente desde los flujos
Si discrepan, la discrepancia se imprime: un comprobador que nunca se ha contrastado
contra otro no ha demostrado que mide lo que dice medir.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE_ROOT = HERE.parent.parent                  # code/
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

import loader                                                    # noqa: E402
from contracts import PaymentOption, Request                     # noqa: E402
from decision.candidates import (PISO, construir, meses_del_plan)  # noqa: E402
from decision.engine import decide                               # noqa: E402
from decision.ranking import clave                               # noqa: E402
from decision.safety import worst_balance                        # noqa: E402
from finance.view import build_view, simulate                    # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Caso:
    req: Request
    decision: object
    view: object
    planes: tuple
    lo_sim: Decimal          # mínimo del horizonte según finance.view.simulate
    lo_dec: Decimal          # mínimo del horizonte según decision.safety.worst_balance
    minimo: Decimal
    deficit: Decimal
    limpios: list            # planes elegibles con brecha == 0
    rechazos: list           # (option_id, motivo) de las opciones NO elegibles


def _cambios(d) -> tuple[tuple[str, ...], tuple[tuple[str, Decimal], ...]]:
    stops = tuple(c.event_id for c in d.spending_changes if c.kind == "stop")
    reduces = tuple((c.event_id, c.new_amount) for c in d.spending_changes
                    if c.kind == "reduce_to" and c.new_amount is not None)
    return stops, reduces


def _creditos_de(plan) -> tuple:
    """Los créditos que el propio motor le atribuye a los cambios del plan.

    `construir` no guarda los créditos en el Plan; se reconstruyen desde el pool con
    las mismas reglas de decision/changes.py.
    """
    return ()


def por_que_no(o: PaymentOption, view, req: Request) -> str | None:
    """Motivo por el que una opción del dataset NO llega a ser un plan candidato.

    None = la opción sí es elegible en su familia.
    """
    p = view.profile
    if o.payment_method == "installments":
        if "installments" not in p.methods_considered:
            return ("el perfil no acepta installments "
                    f"(considera: {', '.join(p.methods_considered) or 'nada'})")
        if not p.max_installment_months:
            return "max_installment_months vacío: el usuario NO acepta plazos"
        m = meses_del_plan(o)
        if m > p.max_installment_months:
            return (f"{o.number_of_payments} pagos ≈ {m} meses > "
                    f"max_installment_months={p.max_installment_months}")
        return None
    if o.payment_method == "full_payment":
        if "full_payment" not in p.methods_considered:
            return ("el perfil no acepta full_payment "
                    f"(considera: {', '.join(p.methods_considered) or 'nada'})")
        return None
    return f"payment_method desconocido: {o.payment_method}"


def analizar(ds, facts, req: Request) -> Caso | None:
    view = build_view(ds, req, facts)
    options = ds.options_by_request.get(req.request_id, ())
    d = decide(view, req, options)
    if not d.payments:
        return None                      # not_recommended: no hay plan que medir

    stops, reduces = _cambios(d)
    pagos = tuple((p.day, p.amount) for p in d.payments)
    lo_sim = simulate(ds, req, facts, stops, reduces, pagos)
    lo_dec = worst_balance(view, d.payments)
    minimo = view.profile.minimum_balance_to_keep
    if lo_sim >= minimo:
        return None                      # el plan recomendado respeta el piso

    planes = construir(view, req, options)
    limpios = [p for p in planes if p.brecha == 0]
    rechazos = []
    for o in options:
        motivo = por_que_no(o, view, req)
        if motivo:
            rechazos.append((o.payment_option_id, o.payment_method, motivo))
    return Caso(req, d, view, planes, lo_sim, lo_dec, minimo,
                minimo - lo_sim, limpios, rechazos)


def elegido_de(caso: Caso):
    """El Plan que el ranking eligió, identificado por método + option_id."""
    for p in caso.planes:
        if (p.method == caso.decision.recommended_payment_method
                and p.option_id == caso.decision.chosen_option_id
                and tuple((x.day, x.amount) for x in p.payments) ==
                tuple((x.day, x.amount) for x in caso.decision.payments)):
            return p
    return None


def deficit_real(ds, facts, req: Request, plan, minimo: Decimal) -> Decimal:
    """Déficit de un plan CANDIDATO medido con el instrumento de A1, no con su brecha.

    Garantía 2: el comprobador se calibra antes de creerle. `plan.brecha` la produce
    `decision.safety.worst_balance` sobre la curva ya construida; esto la reconstruye
    desde los flujos. Si las dos cifras no coinciden, una de las dos miente.
    """
    stops = tuple(c.event_id for c in plan.changes if c.kind == "stop")
    reduces = tuple((c.event_id, c.new_amount) for c in plan.changes
                    if c.kind == "reduce_to" and c.new_amount is not None)
    pagos = tuple((p.day, p.amount) for p in plan.payments)
    lo = simulate(ds, req, facts, stops, reduces, pagos)
    return max(Decimal(0), minimo - lo)


def _sin_alternativa(ds, c: Caso) -> str:
    """Frase auditable de POR QUÉ no existía un plan limpio para este usuario."""
    n_ds = len(ds.options_by_request.get(c.req.request_id, ()))
    motivos = []
    for _oid, met, m in c.rechazos:
        motivos.append(f"{met}: {m.split('(')[0].strip()}")
    med = sorted((dr for _p, dr in getattr(c, "medidos", [])))
    if len(c.planes) == 1:
        base = (f"única elegible de {n_ds} opciones del dataset; "
                f"{'; '.join(dict.fromkeys(motivos)) or 'sin descartes'}")
    else:
        base = (f"{len(c.planes)} elegibles de {n_ds} opciones; déficits "
                f"{', '.join(str(x) for x in med)}; "
                f"{'; '.join(dict.fromkeys(motivos)) or 'sin descartes'}")
    return base


def _menos_danina(c: Caso) -> str:
    eleg = elegido_de(c)
    med = getattr(c, "medidos", [])
    d_eleg = next((dr for p, dr in med if p is eleg), None)
    otros = sorted(dr for p, dr in med if p is not eleg)
    if not otros:
        return (f"no hay con qué compararla: es el único plan elegible y a tiempo; "
                f"la alternativa es not_recommended (déficit {d_eleg}, "
                f"pero el usuario se queda sin el bien)")
    return (f"déficit {d_eleg} contra {', '.join(str(x) for x in otros)} "
            f"de las demás elegibles")


def _descartes(ds, c: Caso) -> str:
    """Las alternativas que el usuario NO tuvo, cada una con su motivo, en corto."""
    p = c.view.profile
    partes = []
    for oid, met, m in c.rechazos:
        corto = m
        if "no acepta full_payment" in m:
            corto = "full_payment: fuera de `payment_methods_user_will_consider`"
        elif "max_installment_months" in m and "vacío" not in m:
            corto = m.replace("max_installment_months", "máx")
        partes.append(f"`{oid}` {corto}")
    # familias sintéticas que tampoco se pudieron construir
    if "full_payment" not in p.methods_considered:
        partes.append("`wait`: exige aceptar full_payment")
    if "partial_payment" not in p.methods_considered:
        partes.append("`partial`: método no aceptado")
    elif not c.req.allows_partial_payment:
        partes.append("`partial`: `allows_partial_payment=false`")
    elif c.view.earliest_full_payment is None:
        partes.append("`partial`: el importe completo nunca es seguro en 90 días")
    elif c.view.earliest_full_payment > c.req.desired_completion_date:
        partes.append("`partial`: el resto caería después del deadline")
    return "<br>".join(partes)


def emitir_md(ds, casos, defectos, peor_que_otro, miscal, total_con_plan) -> None:
    """Tabla markdown. Se pega tal cual en FALLBACKS.md."""
    print("| # | request_id | método · opción | cuota × n | saldo más bajo | mínimo | "
          "déficit | % del mín | alternativas descartadas (por qué no hay limpia) | "
          "por qué la elegida es la menos dañina |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(sorted(casos,
                                 key=lambda c: int(c.req.request_id.split("_")[-1])), 1):
        cur = c.view.profile.home_currency
        cuota = c.decision.payments[0].amount
        n = len(c.decision.payments)
        pct = (c.deficit / c.minimo * 100) if c.minimo else Decimal(0)
        n_eleg = len(c.planes)
        if n_eleg == 1:
            menos = ("único plan elegible y a tiempo; la única alternativa era "
                     "`not_recommended` (dejar al usuario sin el bien)")
        else:
            otros = sorted(dr for p, dr in getattr(c, "medidos", [])
                           if p is not elegido_de(c))
            if otros and min(otros) < c.deficit:
                menos = (f"**NO lo es**: de {n_eleg} elegibles hay una con déficit "
                         f"{min(otros)} y el motor eligió ésta ({c.deficit}). "
                         f"Ver D-1 — el piso no gobierna esta familia y el déficit es "
                         f"el redondeo de `amount_safe_today`")
            else:
                menos = (f"de {n_eleg} elegibles es la de menor déficit: "
                         f"{c.deficit} contra {', '.join(str(x) for x in otros)}")
        print(f"| {i} | `{c.req.request_id}` "
              f"| {c.decision.recommended_payment_method} · "
              f"`{c.decision.chosen_option_id or '—'}` "
              f"| {cuota} × {n} ({cur}), {c.decision.payments[0].day} → "
              f"{c.decision.payments[-1].day} "
              f"| {c.lo_sim} | {c.minimo} | **{c.deficit}** | {pct:.2f}% "
              f"| {_descartes(ds, c)} "
              f"| {menos} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="salida markdown")
    args = ap.parse_args()

    ds = loader.load()
    try:
        from extraction.facts import load_facts
        facts = load_facts(ds)
    except Exception as exc:                       # sin caché de hechos, seguimos
        print(f"AVISO: hechos no disponibles ({exc}); se audita sólo con CSV",
              file=sys.stderr)
        facts = {"images": {}, "messages": {}}

    casos: list[Caso] = []
    total_con_plan = 0
    for req in ds.requests:
        try:
            c = analizar(ds, facts, req)
        except Exception as exc:
            print(f"  !! {req.request_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        total_con_plan += 1
        if c:
            casos.append(c)

    # ── cada alternativa se remide con el instrumento de A1 ──────────────────
    #   defecto real   = existe OTRO plan elegible con déficit REAL 0 y elegimos uno roto
    #   defecto de rank= existe otro plan elegible con déficit REAL MENOR que el elegido
    #   miscalibración = brecha del motor != déficit real
    defectos, peor_que_otro, miscal = [], [], []
    for c in casos:
        eleg = elegido_de(c)
        c.medidos = []                                   # (plan, deficit_real)
        for p in c.planes:
            dr = deficit_real(ds, facts, c.req, p, c.minimo)
            c.medidos.append((p, dr))
            if abs(dr - p.brecha) > Decimal("0.01"):
                miscal.append((c, p, dr))
        d_eleg = next((dr for p, dr in c.medidos if p is eleg), None)
        otros = [(p, dr) for p, dr in c.medidos if p is not eleg]
        if d_eleg is not None and d_eleg > 0:
            if any(dr == 0 for _, dr in otros):
                defectos.append(c)
            elif otros and min(dr for _, dr in otros) < d_eleg:
                peor_que_otro.append(c)

    print(f"PISO = {PISO!r}")
    print(f"solicitudes con plan recomendado: {total_con_plan} / {len(ds.requests)}")
    print(f"planes que dejan el saldo bajo el mínimo (simulate): {len(casos)}")
    print(f"  · sin NINGUNA alternativa limpia elegible: "
          f"{sum(1 for c in casos if not c.limpios)}")
    print(f"  · con alguna alternativa limpia elegible: "
          f"{sum(1 for c in casos if c.limpios)}")
    print(f"  · déficit sub-céntimo (< 0.01, artefacto de cuantización): "
          f"{sum(1 for c in casos if c.deficit < Decimal('0.01'))}")
    print(f"DEFECTO ALTO — había alternativa LIMPIA y elegimos la que rompe: {len(defectos)}")
    for c in defectos:
        print(f"  !! {c.req.request_id}")
    print(f"DEFECTO — otra alternativa rompía MENOS que la elegida: {len(peor_que_otro)}")
    for c in peor_que_otro:
        print(f"  !! {c.req.request_id}")
    print(f"miscalibración instrumento (|brecha - déficit_real| > 0.01): {len(miscal)}")
    for c, p, dr in miscal[:10]:
        print(f"  ~ {c.req.request_id} {p.method}[{p.option_id}] "
              f"brecha={p.brecha} deficit_real={dr}")
    por_metodo: dict[str, int] = {}
    for c in casos:
        m = c.decision.recommended_payment_method
        por_metodo[m] = por_metodo.get(m, 0) + 1
    print(f"por método: {por_metodo}")
    unicos = sum(1 for c in casos if len(c.planes) == 1)
    print(f"casos con UNA sola opción elegible construida: {unicos}")
    print(f"casos con cambios de gasto en el plan elegido: "
          f"{sum(1 for c in casos if c.decision.spending_changes)}")
    print()

    if args.md:
        emitir_md(ds, casos, defectos, peor_que_otro, miscal, total_con_plan)
        return 0

    for c in sorted(casos, key=lambda c: int(c.req.request_id.split("_")[-1])):
        eleg = elegido_de(c)
        print("─" * 78)
        print(f"{c.req.request_id} · {c.decision.recommended_payment_method} "
              f"[{c.decision.chosen_option_id or '-'}] · {c.decision.affordability_status}")
        print(f"  usuario={c.req.user_id} moneda={c.view.profile.home_currency} "
              f"pedido={c.req.requested_amount} deadline={c.req.desired_completion_date}")
        print(f"  plan   = " + " | ".join(f"{p.day}:{p.amount}" for p in c.decision.payments))
        print(f"  cambios= " + (", ".join(ch.render() for ch in c.decision.spending_changes)
                                or "none"))
        print(f"  saldo más bajo (simulate)     = {c.lo_sim}")
        print(f"  saldo más bajo (worst_balance)= {c.lo_dec}")
        print(f"  mínimo requerido              = {c.minimo}")
        print(f"  déficit                       = {c.deficit}")
        print(f"  brecha que el motor le asigna = "
              f"{eleg.brecha if eleg is not None else 'plan no identificado'}")
        print(f"  planes elegibles construidos  = {len(c.planes)} "
              f"(limpios según el motor: {len(c.limpios)})")
        med = {id(p): dr for p, dr in getattr(c, "medidos", [])}
        for p in sorted(c.planes, key=clave(c.req)):
            marca = "*" if p is eleg else " "
            print(f"    {marca} {p.method}[{p.option_id or '-'}] brecha={p.brecha} "
                  f"deficit_real={med.get(id(p))} "
                  f"total={p.total_paid} pagos={len(p.payments)} "
                  f"cambios={len(p.changes)} fin={p.last_day}")
        print(f"  perfil: metodos={list(c.view.profile.methods_considered)} "
              f"max_meses={c.view.profile.max_installment_months}")
        print(f"  opciones del dataset: {len(ds.options_by_request.get(c.req.request_id, ()))}")
        for oid, met, motivo in c.rechazos:
            print(f"    x {oid} [{met}] -> {motivo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
