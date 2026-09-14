"""Recurrencia de un gasto flexible: cuándo vuelve a cobrarse y cuánto libera pararlo.

Un cambio de gasto no libera dinero "hoy": libera el importe de CADA cobro futuro que
ya no ocurre. Sin las fechas de esos cobros no se puede saber si el cambio alcanza.

La serie se agrupa por (usuario, event_type, category, flexibility). Medido sobre el
corpus real: 608 grupos; los 76 con varias descripciones son todos gasto variable
(dining) con nombres distintos para el mismo hábito — son UNA serie, no varias.
Cadencias medidas: 30/31 (mensual, 453 grupos), 14, 21 y 7 días.

Mutación falsadora: proyectar sólo UNA ocurrencia futura pone en rojo
test_series.py::test_ahorro_se_acumula_en_cada_cobro.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from contracts import Event, FinanceView, Request

from .safety import horizon_end

_HIST_CACHE: dict[str, tuple[Event, ...]] | None = None


def _dataset_history(user_id: str) -> tuple[Event, ...]:
    """Histórico crudo del usuario. Sólo lectura; si no se puede cargar, vacío."""
    global _HIST_CACHE
    if _HIST_CACHE is None:
        try:
            import loader
            _HIST_CACHE = dict(loader.load().events_by_user)
        except Exception:
            _HIST_CACHE = {}
    return _HIST_CACHE.get(user_id, ())


def _key(e: Event) -> tuple[str, str, str]:
    return (e.event_type, e.category, e.flexibility)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


@dataclass(frozen=True)
class Serie:
    """Una serie recurrente flexible, con su próximo calendario."""
    event: Event                    # la ocurrencia más reciente: el event_id que va al CSV
    amount: Decimal                 # importe proyectado de cada cobro
    cadence_days: int
    monthly: bool
    occurrences: tuple[date, ...]   # cobros futuros dentro del horizonte

    @property
    def event_id(self) -> str:
        return self.event.event_id


def _cadence(days: list[date]) -> tuple[int, bool]:
    uniq = sorted(set(days))
    if len(uniq) < 2:
        return 30, True
    gaps = [(b - a).days for a, b in zip(uniq, uniq[1:]) if (b - a).days > 0]
    if not gaps:
        return 30, True
    med = int(round(statistics.median(gaps)))
    return (med, True) if 27 <= med <= 32 else (max(1, med), False)


def _occurrences(last: date, cad: int, monthly: bool,
                 start: date, end: date) -> tuple[date, ...]:
    out, k = [], 1
    while k <= 400:
        d = _add_months(last, k) if monthly else last + timedelta(days=cad * k)
        if d > end:
            break
        if d > start:
            out.append(d)
        k += 1
    return tuple(out)


def build_series(view: FinanceView, req: Request) -> tuple[Serie, ...]:
    """Series flexibles vivas al día del request, con su calendario futuro."""
    hist = _dataset_history(req.user_id) or view.flexible_events
    pool = view.flexible_events or hist
    start, end = req.request_date, horizon_end(req.request_date)

    grupos: dict[tuple[str, str, str], list[Event]] = {}
    for e in pool:
        if e.flexibility in ("", "fixed") or e.direction != "debit":
            continue
        if e.event_date is None or e.event_date > start:
            continue
        grupos.setdefault(_key(e), []).append(e)

    hist_por_clave: dict[tuple[str, str, str], list[Event]] = {}
    for e in hist:
        if e.flexibility in ("", "fixed") or e.direction != "debit":
            continue
        if e.event_date is not None and e.event_date <= start:
            hist_por_clave.setdefault(_key(e), []).append(e)

    series: list[Serie] = []
    for k, evs in grupos.items():
        rep = max(evs, key=lambda e: e.event_date)
        if rep.amount is None:
            continue                                  # sin monto no se puede medir el ahorro
        h = sorted(hist_por_clave.get(k, evs), key=lambda e: e.event_date)
        cad, monthly = _cadence([e.event_date for e in h])
        montos = [e.amount for e in h[-3:] if e.amount is not None] or [rep.amount]
        amount = sum(montos, Decimal(0)) / len(montos)
        occ = _occurrences(max(h[-1].event_date, rep.event_date), cad, monthly, start, end)
        if occ:
            series.append(Serie(rep, amount, cad, monthly, occ))
    return tuple(series)
