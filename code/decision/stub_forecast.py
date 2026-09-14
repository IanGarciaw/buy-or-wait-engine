"""ANDAMIO DE MEDICIÓN — no es parte del pipeline de producción.

A1 (code/finance/) es el dueño del forecast. Mientras no exista, calibrar.py necesita
una curva de caja para poder medir el motor de decisión. Esto la fabrica con las reglas
del enunciado, y calibrar.py compara su `amount_safe_today` contra la columna
`amount_safe_to_pay` del ground truth para saber CUÁNTO vale la medición.

Reglas de caja aplicadas (§90-Day Safety Check):
  · débito pendiente  -> se reserva          · crédito pendiente -> NO se cuenta
  · scheduled         -> entra en su fecha   · cancelled/failed  -> se descarta
  · unrealized/non_cash -> nunca es efectivo
  · recurrencia sólo cuando el histórico la sostiene (≥3 ocurrencias)
No lee mensajes ni imágenes: por eso los 16 eventos sin monto quedan fuera aquí.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta
from decimal import Decimal

from contracts import (CashPoint, Event, FinanceView, Forecast, HORIZON_DAYS,
                       Profile, Request)

import loader

_VIVOS = ("settled", "pending", "scheduled")


def _fecha(e: Event) -> date | None:
    return e.settlement_date or e.event_date


def _cuenta(e: Event) -> bool:
    if e.direction == "non_cash" or e.status in ("cancelled", "failed", "unrealized"):
        return False
    if e.status == "pending" and e.direction == "credit":
        return False          # un crédito pendiente no es dinero
    return e.status in _VIVOS and e.amount is not None


def _signo(e: Event) -> int:
    return -1 if e.direction == "debit" else 1


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def _flujos(ds, prof: Profile, req: Request) -> list[tuple[date, Decimal]]:
    """(día, delta) de todo lo que mueve caja después del request_date."""
    start, end = req.request_date, req.request_date + timedelta(days=HORIZON_DAYS)
    evs = [e for e in ds.events_by_user.get(req.user_id, ()) if _cuenta(e)]
    out: list[tuple[date, Decimal]] = []

    for e in evs:                                   # lo explícito primero
        d = _fecha(e)
        if d and start < d <= end:
            amt = loader.convert(e.amount, e.currency, prof.home_currency, d, ds.rates)
            out.append((d, Decimal(_signo(e)) * amt))

    grupos: dict[tuple, list[Event]] = {}
    for e in evs:
        grupos.setdefault((e.event_type, e.category, e.direction), []).append(e)

    for _, g in grupos.items():
        fechas = sorted({_fecha(e) for e in g if _fecha(e)})
        if len(fechas) < 2:
            continue                                # sin historia no hay recurrencia
        gaps = [(b - a).days for a, b in zip(fechas, fechas[1:]) if (b - a).days > 0]
        if not gaps:
            continue
        cad = int(round(statistics.median(gaps)))
        if cad < 1 or cad > 45:
            continue
        mensual = 27 <= cad <= 32
        ult = fechas[-1]
        orden = sorted(g, key=lambda e: _fecha(e) or start)
        # Ingreso: se proyecta el último confirmado (el salario ya notificado).
        # Gasto variable: media de los últimos 3, que es la lectura conservadora.
        recientes = orden[-1:] if g[0].direction == "credit" else orden[-3:]
        montos = [loader.convert(e.amount, e.currency, prof.home_currency,
                                 _fecha(e), ds.rates) for e in recientes]
        monto = Decimal(_signo(g[0])) * (sum(montos, Decimal(0)) / len(montos))
        for k in range(1, 400):
            d = _add_months(ult, k) if mensual else ult + timedelta(days=cad * k)
            if d > end:
                break
            if d > start:
                out.append((d, monto))
    return out


def build_view(ds, req: Request) -> FinanceView:
    prof = ds.profiles[req.user_id]
    start, end = req.request_date, req.request_date + timedelta(days=HORIZON_DAYS)
    flujos = _flujos(ds, prof, req)

    saldo = prof.current_available_balance
    puntos = [CashPoint(start, saldo)]
    por_dia: dict[date, Decimal] = {}
    for d, v in flujos:
        por_dia[d] = por_dia.get(d, Decimal(0)) + v
    for d in sorted(por_dia):
        saldo += por_dia[d]
        puntos.append(CashPoint(d, saldo, "proyectado"))

    minimo = min(p.balance for p in puntos)
    dia_min = next(p.day for p in puntos if p.balance == minimo)
    fc = Forecast(req.user_id, start, tuple(puntos), minimo, dia_min)

    headroom = max(Decimal(0), minimo - prof.minimum_balance_to_keep)
    safe = min(headroom, req.requested_amount)

    # earliest: primer día en que pagar el importe COMPLETO deja la curva sobre el mínimo
    # de ahí en adelante (sin cambios de gasto).
    objetivo = prof.minimum_balance_to_keep + req.requested_amount
    earliest = None
    dias = sorted({p.day for p in puntos} | {start})
    for d in dias:
        if d < start or d > end:
            continue
        resto = [p.balance for p in puntos if p.day >= d]
        if resto and min(resto) >= objetivo:
            earliest = d
            break

    flex: dict[tuple, Event] = {}
    for e in ds.events_by_user.get(req.user_id, ()):
        if e.flexibility in ("", "fixed") or e.direction != "debit":
            continue
        if e.event_date and e.event_date <= start:
            k = (e.event_type, e.category, e.flexibility)
            if k not in flex or e.event_date > flex[k].event_date:
                flex[k] = e

    return FinanceView(prof, fc, safe, earliest, tuple(flex.values()),
                       confidence=0.5, notes=("stub_forecast: NO es A1",))
