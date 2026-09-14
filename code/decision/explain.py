"""La explicación no se inventa: se calca del ground truth de los 25 samples.

Plantillas observadas (una por método) y el número que todas citan al final es
`minimum_balance_to_keep`, con separador de millar — verificado contra los 25 perfiles.

  full_payment hoy        -> "Pay {C} {x} today. This leaves at least {C} {min} available
                             over the next 90 days."
  full_payment + cambios  -> "Stop the …[ and reduce the … to {C} {y}], then pay {C} {x}
                             today. This leaves at least {C} {min} available."
  installments            -> "Use {n} installments of {C} {x}, starting {fecha}. This
                             leaves at least {C} {min} available."
  wait                    -> "Pay {C} {x} in full on {fecha}. Paying earlier would take
                             the balance below the {C} {min} minimum."
  partial_payment         -> "Pay {C} {a} today and the remaining {C} {b} on {fecha}. This
                             completes the full request and keeps the {C} {min} minimum
                             protected."
  not_recommended         -> dos variantes; ver `_no_recomendado`.

HIPÓTESIS (base delgada: 7 casos) — el ground truth usa la variante que menciona el
importe disponible cuando éste es una fracción NO trivial de lo pedido (11.0% y 12.2%)
y la variante corta cuando es marginal (1.8%–4.8%). Umbral 8%, a mitad del hueco.
Sólo afecta a la prosa: ningún campo estructurado depende de él.
"""
from __future__ import annotations

from decimal import Decimal

from contracts import Decision, FinanceView, Request, SpendingChange

from .candidates import Plan
from .fmt import long_date, money

UMBRAL_MENCIONA_DISPONIBLE = Decimal("0.08")


def _cambio_frase(c: SpendingChange, desc: str, cur: str) -> str:
    d = (desc or "expense").strip().lower()
    if c.kind == "stop":
        return f"stop the {d}"
    return f"reduce the {d} to {cur} {money(c.new_amount)}"


def _cambios_texto(plan: Plan, cur: str) -> str:
    descs = list(plan.descripciones) + [""] * len(plan.changes)
    partes = [_cambio_frase(c, d, cur) for c, d in zip(plan.changes, descs)]
    if not partes:
        return ""
    txt = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " and " + partes[-1]
    return txt[0].upper() + txt[1:]


def _no_recomendado(view: FinanceView, req: Request, cur: str, minimo: str) -> str:
    safe = min(view.amount_safe_today, req.requested_amount)
    if req.requested_amount > 0 and safe / req.requested_amount >= UMBRAL_MENCIONA_DISPONIBLE:
        return (f"Do not proceed with the {cur} {money(req.requested_amount)} request. "
                f"Although {cur} {money(safe)} is available today, the full amount "
                f"cannot be completed safely within 90 days.")
    return (f"Do not make this payment by {long_date(req.desired_completion_date)}. "
            f"None of the available options keeps the {cur} {minimo} minimum protected.")


def explicar(view: FinanceView, req: Request, plan: Plan | None) -> str:
    cur = view.profile.home_currency
    minimo = money(view.profile.minimum_balance_to_keep)
    if plan is None:
        return _no_recomendado(view, req, cur, minimo)

    monto = money(req.requested_amount)
    if plan.method == "full_payment":
        if plan.changes:
            return (f"{_cambios_texto(plan, cur)}, then pay {cur} {monto} today. "
                    f"This leaves at least {cur} {minimo} available.")
        return (f"Pay {cur} {monto} today. This leaves at least {cur} {minimo} "
                f"available over the next 90 days.")

    if plan.method == "wait":
        d = long_date(plan.payments[0].day)
        return (f"Pay {cur} {monto} in full on {d}. Paying earlier would take the "
                f"balance below the {cur} {minimo} minimum.")

    if plan.method == "partial_payment":
        a, b = plan.payments
        base = (f"Pay {cur} {money(a.amount)} today and the remaining "
                f"{cur} {money(b.amount)} on {long_date(b.day)}. This completes the "
                f"full request and keeps the {cur} {minimo} minimum protected.")
        return f"{_cambios_texto(plan, cur)}, then {base[0].lower()}{base[1:]}" \
            if plan.changes else base

    n = len(plan.payments)
    cuota = money(plan.payments[0].amount)
    base = (f"Use {n} installments of {cur} {cuota}, starting "
            f"{long_date(plan.first_day)}. This leaves at least {cur} {minimo} available.")
    return f"{_cambios_texto(plan, cur)}, then {base[0].lower()}{base[1:]}" \
        if plan.changes else base
