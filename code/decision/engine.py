"""decide(): una sola puerta. Filtra, rankea, y sólo entonces nombra el estado.

`affordability_status` NO es una opinión: sale del plan elegido.
  full_payment hoy sin cambios -> affordable_now      (y el usuario acepta full_payment,
                                  garantizado por el filtro de elegibilidad)
  full_payment con cambios     -> affordable_with_plan
  installments / partial       -> affordable_with_plan
  wait                         -> affordable_later
  ningún plan                  -> not_affordable
Los 25 samples no tienen ni una excepción a esta tabla. request_12 la prueba por el lado
incómodo: el importe completo era seguro HOY (safe == requested, earliest == request_date)
y aun así el estado es affordable_with_plan, porque user_12 no acepta full_payment.

`amount_safe_to_pay` y `earliest_date_for_full_payment` son medidas de CAPACIDAD: salen
de A1 tal cual, sin cambios de gasto aplicados, aunque el plan elegido sí los use
(request_06: paga hoy con un stop, y la fecha reportada sigue siendo el 15-ene).

Mutación falsadora: devolver affordable_now cuando el plan trae cambios de gasto pone en
rojo test_engine.py::test_cambios_degradan_a_affordable_with_plan.
"""
from __future__ import annotations

from decimal import Decimal

from contracts import (Decision, FinanceView, PaymentOption, Request)

from .candidates import Plan, construir
from .explain import explicar
from .ranking import mejor, traza
from .safety import worst_balance


def _estado(plan: Plan | None, safe: Decimal, req: Request) -> str:
    if plan is None:
        return "not_affordable"
    if plan.method == "wait":
        return "affordable_later"
    if plan.method == "full_payment" and not plan.changes and safe >= req.requested_amount:
        return "affordable_now"
    return "affordable_with_plan"


def _traza(view: FinanceView, req: Request, planes, plan: Plan | None) -> str:
    """Por qué ganó éste y no otro, más el dato de curva que A4 puede querer mirar.

    `curva90` es el punto más bajo de la curva de A1 con el plan elegido encima. NO es
    una compuerta: el corpus refutó esa lectura (ver decision/capacidad.py). Va en la
    traza para que el verificador pueda señalar un plan que la prueba mensual aprueba y
    la curva no.
    """
    base = traza(planes, req, plan) or "sin planes elegibles"
    if plan is None:
        return base
    return f"{base} || curva90={worst_balance(view, plan.payments)}"


def decide(view: FinanceView, req: Request,
           options: tuple[PaymentOption, ...]) -> Decision:
    safe = min(max(Decimal(0), view.amount_safe_today), req.requested_amount)
    earliest = view.earliest_full_payment

    planes = construir(view, req, options)
    plan = mejor(planes, req)
    estado = _estado(plan, safe, req)

    if estado == "affordable_now":
        earliest = req.request_date      # el enunciado lo exige, no lo sugiere
    # NO se vacía `earliest` por el estado. La condición del enunciado es sobre la
    # CAPACIDAD, no sobre la recomendación:
    #
    #   "Leave it empty when the full amount is not expected to become safe within
    #    the forecast period."                        (línea 113)
    #   "`earliest_date_for_full_payment` measures financial capacity INDEPENDENTLY
    #    of the user's payment-method preferences. It may equal `request_date` even
    #    when the selected recommendation is installments because the user has
    #    chosen not to consider full payment."        (línea 163)
    #
    # `earliest` ya viene de `finance/` como None cuando el importe completo no se
    # vuelve seguro dentro del horizonte. Eso es la regla. Vaciarlo además porque el
    # estado sea `not_affordable` mete la preferencia de método por la puerta de
    # atrás, que es justo lo que la línea 163 pone como ejemplo de lo que NO se hace.
    #
    # CORREGIDO EL 13-SEP. La regla anterior se apoyaba en que los 7 samples
    # `not_affordable` traen la celda vacía — cierto, pero redundante: medido, esos 7
    # ya dan `earliest = None` por capacidad, así que el vaciado forzado nunca se
    # activaba ahí. En los 250 sí: borraba la fecha en 41 filas que sí tienen
    # capacidad dentro del horizonte, 25 de ellas sólo porque el usuario no acepta
    # `full_payment`. Un patrón de la muestra no deroga una frase del contrato.

    return Decision(
        request_id=req.request_id,
        amount_safe_to_pay=safe,
        affordability_status=estado,
        recommended_payment_method=plan.method if plan else "not_recommended",
        payments=plan.payments if plan else (),
        earliest_date_for_full_payment=earliest,
        spending_changes=plan.changes if plan else (),
        decision_explanation=explicar(view, req, plan),
        chosen_option_id=plan.option_id if plan else None,
        trace=_traza(view, req, planes, plan),
        confidence=view.confidence,
    )
