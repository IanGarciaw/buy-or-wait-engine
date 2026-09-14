"""La prueba de seguridad de 90 días aplicada a un plan concreto.

El forecast de A1 es la curva SIN el pago del request y SIN cambios de gasto.
Un plan es seguro si, restando sus pagos y sumando el ahorro de los cambios,
la curva nunca baja del mínimo dentro del horizonte.

El mínimo de una curva escalonada sólo puede estar en una discontinuidad: por eso
se evalúa en la unión de (días con punto de forecast) ∪ (días de pago) ∪ (días de ahorro).
Mutación falsadora: evaluar sólo en los días con punto deja pasar un plan cuyo pago
cae entre dos puntos — test_safety.py::test_dia_de_pago_sin_punto_de_forecast lo caza.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from contracts import HORIZON_DAYS, CashPoint, FinanceView, Payment

Credit = tuple[date, Decimal]     # (día, monto liberado por un cambio de gasto)


def horizon_end(start: date) -> date:
    return start + timedelta(days=HORIZON_DAYS)


def balance_at(points: tuple[CashPoint, ...], day: date,
               fallback: Decimal) -> Decimal:
    """Curva escalonada: vale el último punto con día <= day.

    Sin puntos no hay ausencia de información: se usa el fallback conservador
    (el mínimo declarado por el forecast), nunca cero.
    """
    if not points:
        return fallback
    best = None
    for p in points:
        if p.day <= day and (best is None or p.day > best.day):
            best = p
    return best.balance if best is not None else points[0].balance


def worst_balance(view: FinanceView, payments: tuple[Payment, ...],
                  credits: tuple[Credit, ...] = ()) -> Decimal:
    """El punto más bajo de la curva con el plan aplicado, en los 90 días completos.

    LA VENTANA ES EL HORIZONTE DE PRONÓSTICO ENTERO. El enunciado no deja margen:

        "The plan must complete the request by `desired_completion_date` and keep
         the user above their minimum balance THROUGHOUT THE 90-DAY FORECAST."
        (problem_statement.md, línea 180)

        "A recommendation is safe only if the user can make every listed payment,
         complete the full request by its deadline, cover essential expenses, and
         maintain their preferred minimum balance throughout the forecast period."
        (línea 25)

    REVERTIDO EL 13-SEP, y la historia importa. Se midió que cerrar la ventana en el
    último pago del plan hacía pasar los 18 planes del ground truth en vez de 11, sin
    cambiar ni una de las 250 filas de salida. Esa coincidencia es real y queda
    registrada como **artefacto de investigación** en shadow/, pero NO justifica la
    regla: el contrato es inequívoco y una coincidencia de 18 ejemplos no deroga una
    frase del enunciado.

    La lectura correcta de aquel 18/18 no es "el generador usa la ventana del último
    pago", sino: "en esos 7 casos nuestro pronóstico difiere del que produjo las
    etiquetas". Es una medición sobre NUESTRA curva, no sobre su regla.
    """
    f = view.forecast
    start, end = f.start, horizon_end(f.start)
    days = {p.day for p in f.points}
    days |= {p.day for p in payments}
    days |= {d for d, _ in credits}
    days.add(start)
    days = sorted(d for d in days if start <= d <= end)
    worst = None
    for d in days:
        bal = balance_at(f.points, d, f.min_balance)
        out = sum((p.amount for p in payments if p.day <= d), Decimal(0))
        add = sum((a for dd, a in credits if dd <= d), Decimal(0))
        v = bal - out + add
        if worst is None or v < worst:
            worst = v
    return worst if worst is not None else f.min_balance


def is_safe(view: FinanceView, payments: tuple[Payment, ...],
            credits: tuple[Credit, ...] = ()) -> bool:
    return worst_balance(view, payments, credits) >= view.profile.minimum_balance_to_keep
