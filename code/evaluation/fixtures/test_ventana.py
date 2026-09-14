#!/usr/bin/env python3
"""La ventana de seguridad del plan es el HORIZONTE COMPLETO de 90 días.

El enunciado no deja margen:

    "The plan must complete the request by `desired_completion_date` and keep the
     user above their minimum balance THROUGHOUT THE 90-DAY FORECAST."
    (problem_statement.md, línea 180)

    "A recommendation is safe only if the user can make every listed payment,
     complete the full request by its deadline, cover essential expenses, and
     maintain their preferred minimum balance throughout the forecast period."
    (línea 25)

HISTORIA, porque explica por qué este archivo existe. El 13-sep se midió que cerrar
la ventana en el último pago del plan hacía pasar los 18 planes del ground truth en
vez de 11, sin cambiar una sola de las 250 filas de salida. La coincidencia es real y
queda registrada en `shadow/` como artefacto de investigación — pero NO deroga una
frase inequívoca del contrato. Una coincidencia de 18 ejemplos no reinterpreta el
enunciado; lo que indica es que en esos 7 casos NUESTRO pronóstico difiere del que
produjo las etiquetas, que es una medición sobre nuestra curva, no sobre su regla.

Estas pruebas fijan la conducta correcta para que nadie vuelva a moverla sin verlo.

    python3 code/evaluation/fixtures/test_ventana.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts import CashPoint, FinanceView, Forecast, Payment, Profile  # noqa: E402
from decision.safety import horizon_end, worst_balance                     # noqa: E402

D = Decimal
HOY = date(2026, 3, 1)
MINIMO = D("1000")
fallos: list[str] = []


def vista(curva: list[tuple[date, str]]) -> FinanceView:
    pts = tuple(CashPoint(d, D(b)) for d, b in curva)
    lo = min(pts, key=lambda p: p.balance)
    prof = Profile(user_id="u_test", home_currency="EUR",
                   current_available_balance=D("5000"),
                   minimum_balance_to_keep=MINIMO,
                   financial_priorities=(), protect=(), willing_reduce=(),
                   willing_stop=(), methods_considered=("full_payment", "installments"),
                   max_installment_months=12)
    fc = Forecast(user_id="u_test", start=HOY, points=pts,
                  min_balance=lo.balance, min_balance_day=lo.day)
    return FinanceView(profile=prof, forecast=fc, amount_safe_today=D("4000"),
                       earliest_full_payment=HOY, flexible_events=())


def comprobar(nombre: str, cond: bool, detalle: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FALLA'}  {nombre}")
    if detalle:
        print(f"          {detalle}")
    if not cond:
        fallos.append(nombre)


print(__doc__.split("\n")[0])
print("=" * 78)

# CASO A — un gasto POSTERIOR al último pago SÍ invalida el plan.
#   Es el caso crítico: la ventana no se cierra con el plan, llega a los 90 días.
print("\nCASO A · gasto grande el día 80, última cuota el día 60  ← EL CASO CRÍTICO")
v = vista([(HOY, "5000"),
           (HOY + timedelta(days=60), "4500"),
           (HOY + timedelta(days=80), "100")])
pagos = (Payment(HOY, D("400")),
         Payment(HOY + timedelta(days=30), D("400")),
         Payment(HOY + timedelta(days=60), D("400")))
w = worst_balance(v, pagos)
comprobar("un gasto posterior al último pago SÍ invalida el plan",
          w < MINIMO,
          f"peor saldo en 90 días = {w} < {MINIMO}; el saldo cae a 100 el día 80, "
          f"20 días después de la última cuota, y eso cuenta")

# CASO B — un pago único hoy también se juzga a 90 días.
print("\nCASO B · pago único hoy · la ventana sigue siendo de 90 días")
v = vista([(HOY, "5000"), (HOY + timedelta(days=40), "200")])
pagos = (Payment(HOY, D("1500")),)
w = worst_balance(v, pagos)
comprobar("un pago de hoy se juzga hasta el día 90, no hasta hoy",
          w < MINIMO,
          f"peor saldo = {w}; la curva cae a 200 el día 40 y eso invalida el plan")

# CASO C — una curva que aguanta los 90 días completos sí pasa.
print("\nCASO C · control positivo · una curva sana pasa")
v = vista([(HOY, "5000"),
           (HOY + timedelta(days=45), "4200"),
           (HOY + timedelta(days=89), "3800")])
pagos = (Payment(HOY, D("500")),
         Payment(HOY + timedelta(days=30), D("500")))
w = worst_balance(v, pagos)
comprobar("una curva que aguanta los 90 días completos pasa",
          w >= MINIMO,
          f"peor saldo = {w} >= {MINIMO}")

# CASO D — la ventana termina EXACTAMENTE en el día 90, no antes ni después.
print("\nCASO D · la ventana se cierra en request_date + 90")
comprobar("horizon_end = request_date + 90 días",
          horizon_end(HOY) == HOY + timedelta(days=90),
          f"horizon_end({HOY}) = {horizon_end(HOY)}")
v = vista([(HOY, "5000"),
           (HOY + timedelta(days=95), "10")])          # fuera del horizonte
w = worst_balance(v, (Payment(HOY, D("500")),))
comprobar("un evento del día 95 queda fuera y no invalida",
          w >= MINIMO,
          f"peor saldo = {w}; el desplome del día 95 está fuera de los 90 días")

print("\n" + "=" * 78)
if fallos:
    print(f"ROJO · {len(fallos)} comprobación(es) fallaron: {fallos}")
    sys.exit(1)
print("VERDE · la ventana del plan es el horizonte de 90 días, como exige el enunciado")
print("=" * 78)
