"""Formato para la PROSA de la explicación. Los montos del CSV los escribe el contrato.

`contracts.fmt_money` (payment_plan y spending_changes_needed) y `contracts.fmt_amount`
(amount_safe_to_pay) son dos reglas distintas del mismo CSV, y el contrato ya las separa.
Aquí sólo vive lo que el contrato no cubre: el formato con separador de millar y la fecha
larga que el ground truth usa DENTRO de decision_explanation
('IDR 15,952,906.67', '8 December 2024').

Mutación falsadora: quitar el separador de millar pone en rojo
test_decision.py::test_explicacion_cita_el_minimo_con_separador.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from contracts import fmt_money

_MESES = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

plan_amount = fmt_money        # alias: el formato del plan es el del contrato


def money(x: Decimal) -> str:
    """Monto para la explicación: con separador de millar."""
    q = Decimal(x).quantize(Decimal("0.01"))
    if q == q.to_integral_value():
        return f"{int(q):,}"
    return f"{q:,.2f}"


def long_date(d: date) -> str:
    """'8 December 2024' — sin cero a la izquierda, como el ground truth."""
    return f"{d.day} {_MESES[d.month - 1]} {d.year}"
