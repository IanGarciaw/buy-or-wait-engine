"""Detección de recurrencia. El punto más difícil del reto y el de mayor impacto.

El enunciado sólo dice "detect recurrence only when history supports it". La regla
concreta se DESCUBRIÓ midiendo contra los 25 samples resueltos, no se asumió.

Qué se descubrió (ver `calibrar.py`):

  · El agrupador correcto es (event_type, category), NO la descripción. Las series
    variables del dataset cambian de texto en cada ocurrencia ("Supermarket basket",
    "Household groceries", "Local market purchase" son la MISMA compra semanal de
    groceries). Agrupar por descripción las parte en singletons y el forecast se
    queda corto.
  · La cadencia mensual no es "cada 30 días" sino "el mismo día del mes". Proyectar
    con 30 días corre la fecha y descoloca el mínimo de la curva.
  · Un ingreso NUNCA se proyecta por inercia: hay series cuyo propio texto declara
    que terminaron ("Final employer payroll"). Proyectarlas inventa ingreso.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from contracts import Event

from . import params


# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Series:
    """Una serie recurrente reconstruida a partir de la historia liquidada."""
    key: tuple
    direction: str            # debit | credit
    event_type: str
    category: str
    period_kind: str          # "month" (día del mes) | "days"
    period: int               # día del mes, o número de días
    amount: Decimal           # ya en moneda del usuario
    last_day: date
    occurrences: int
    sample: Event             # la ocurrencia MÁS RECIENTE (la que cita el output)
    varies: bool              # montos distintos entre ocurrencias


def _median(xs: list) -> Decimal:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return Decimal(0)
    if n % 2:
        return Decimal(s[n // 2])
    return (Decimal(s[n // 2 - 1]) + Decimal(s[n // 2])) / 2


def _pq(xs: list[Decimal], q: Decimal) -> Decimal:
    """Cuantil q por interpolación lineal. q=0.5 es la mediana."""
    s = sorted(xs)
    if not s:
        return Decimal(0)
    i = (len(s) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def _p75(xs: list[Decimal]) -> Decimal:
    return _pq(xs, Decimal("0.75"))


def _tendencia(xs: list[Decimal]) -> Decimal:
    """Siguiente valor por mínimos cuadrados sobre las ocurrencias recientes.

    El gasto esencial no es ruido alrededor de un nivel fijo: la luz sube con la
    estación, el súper con la inflación. Si la serie DERIVA, el mejor predictor del
    próximo recibo es la recta, no la mediana de un año. Se recorta al rango
    histórico para que una racha corta no dispare la extrapolación.
    """
    n = len(xs)
    if n < 3:
        return xs[-1]
    mx = Decimal(n - 1) / 2
    my = sum(xs) / Decimal(n)
    num = sum((Decimal(i) - mx) * (xs[i] - my) for i in range(n))
    den = sum((Decimal(i) - mx) ** 2 for i in range(n))
    if den == 0:
        return my
    pend = num / den
    prox = xs[-1] + pend
    return max(min(prox, max(xs)), min(xs))


def estimate(amounts: list[Decimal], how: str) -> Decimal:
    """Estimador del monto de una serie variable.

    Barrido sobre los 25 samples (KPI = `safe` dentro de ±1%):
      last=7/25 · median=8/25 · p75=6/25 · max=3/25 · **mean=16/25**

    La media gana por una razón estructural, no por suerte: el dataset genera el
    gasto variable como ruido simétrico alrededor de un nivel, y sobre un horizonte
    de 90 días se acumulan ~13 muestras. La media es el estimador insesgado de esa
    suma; la mediana la subestima cuando la cola es larga y el máximo la infla hasta
    volver "not_affordable" casos que la verdad de campo declara asequibles.
    """
    if not amounts:
        return Decimal(0)
    xs = amounts[-params.LOOKBACK:] if (params.LOOKBACK and not params.LOOKBACK_DAYS) \
        else amounts
    if how == "trend":
        return _tendencia(xs)
    if how == "trend_med":
        return (_tendencia(xs) + _median(xs)) / 2
    if how.startswith("p") and how[1:].isdigit():
        return _pq(xs, Decimal(how[1:]) / 100)
    if how == "median":
        return _median(xs)
    if how == "max":
        return max(xs)
    if how == "last":
        return xs[-1]
    return sum(xs) / Decimal(len(xs))          # mean


def _clamp_day(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def add_months(d: date, n: int, anchor_day: int) -> date:
    m = d.month - 1 + n
    return _clamp_day(d.year + m // 12, m % 12 + 1, anchor_day)


# ─────────────────────────────────────────────────────────────────────────────

def _cadence(days: list[date], min_occ: int) -> tuple[str, int] | None:
    """¿Hay cadencia? Devuelve ("month", día_del_mes) o ("days", n), o None."""
    if len(days) < min_occ:
        return None
    gaps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < min_occ - 1:
        return None
    med = _median(gaps)
    if med <= 0:
        return None
    tol = max(Decimal(1), med * Decimal(str(params.INTERVAL_TOL)))
    agree = sum(1 for g in gaps if abs(Decimal(g) - med) <= tol)
    if Decimal(agree) / Decimal(len(gaps)) < Decimal(str(params.INTERVAL_AGREE)):
        return None

    if params.MONTH_MIN <= med <= params.MONTH_MAX:
        # Mensual: el ancla es el día del mes que más se repite en lo reciente.
        recent = days[-params.LOOKBACK:] if params.LOOKBACK else days
        counts: dict[int, int] = {}
        for d in recent:
            counts[d.day] = counts.get(d.day, 0) + 1
        anchor = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        return ("month", anchor)

    n = int(med.to_integral_value())
    return ("days", max(1, n))


def _terminal(desc: str) -> bool:
    d = " " + desc.lower().strip() + " "
    return any(w in d for w in params.INCOME_TERMINAL)


def _not_recurring_income(desc: str) -> bool:
    d = desc.lower()
    return any(w in d for w in params.INCOME_NOT_RECURRING)


def norm(desc: str) -> str:
    """Descripción normalizada: minúsculas, sin dígitos, sin espacios de más."""
    return " ".join("".join(c for c in desc.lower() if not c.isdigit()).split())


def _build(key: tuple, rows: list, as_of: date) -> Series | None:
    rows.sort(key=lambda r: (r[1], r[0].event_id))
    direction, etype, cat = key[0], key[1], key[2]
    if direction == "credit" and etype == "income":
        # Un bono trimestral, una comisión o un premio NO son nómina. Se sacan
        # antes de medir la cadencia y antes de estimar el importe; si no, ni el
        # intervalo ni la media describen el sueldo.
        limpio = [r for r in rows if not _not_recurring_income(r[0].description)]
        if len(limpio) >= params.MIN_OCCURRENCES_INCOME:
            rows = limpio
    days = [r[1] for r in rows]
    min_occ = (params.MIN_OCCURRENCES_INCOME
               if direction == "credit" and etype == "income"
               else params.MIN_OCCURRENCES)
    cad = _cadence(days, min_occ)
    if cad is None:
        return None
    kind, per = cad

    if direction == "credit":
        if etype != "income":
            return None                    # reembolsos y devoluciones: nunca
        if _terminal(rows[-1][0].description):
            return None                    # el dato declara que fue el último

    span = 30 if kind == "month" else per
    if (as_of - days[-1]).days > span * params.STALE_FACTOR + params.STALE_SLACK:
        return None                        # se saltó su cita: la serie murió

    usar = rows
    if params.LOOKBACK_DAYS:
        corte = days[-1] - timedelta(days=params.LOOKBACK_DAYS)
        recientes = [r for r in rows if r[1] >= corte and r[2] is not None]
        if len(recientes) >= 2:
            usar = recientes
    amounts = [r[2] for r in usar if r[2] is not None]
    if not amounts:
        return None
    how = (params.INCOME_ESTIMATOR
           if direction == "credit" and etype == "income" else params.ESTIMATOR)
    amt = estimate(amounts, how)
    varies = len({a.quantize(Decimal("0.01")) for a in amounts}) > 1
    if varies and direction == "debit":
        amt = amt * Decimal(str(params.VARIABLE_MULT))
    return Series(key=key, direction=direction, event_type=etype, category=cat,
                  period_kind=kind, period=per, amount=amt, last_day=days[-1],
                  occurrences=len(rows), sample=rows[-1][0], varies=varies)


def detect(history: list[tuple[Event, date, Decimal | None]],
           as_of: date) -> list[Series]:
    """`history` = (evento, día de liquidación, monto en moneda del usuario), ya
    filtrado a lo LIQUIDADO y anterior o igual a `as_of`.

    El monto puede ser None (monto en blanco que vive en una imagen). El VACÍO NO ES
    AUSENCIA: esa ocurrencia sigue contando para la CADENCIA — es la que fija el
    ancla de la serie — y sólo se excluye del estimador de monto. Ignorar su fecha
    corre toda la proyección semanal una semana (medido en request_19).

    Agrupa en dos pasadas:
      1ª por (dirección, tipo, categoría). Es la que hace falta para el gasto
         variable, cuyo texto cambia en cada ocurrencia.
      2ª SÓLO si la primera no encontró cadencia: se parte por descripción. Es la
         que hace falta cuando una categoría esconde DOS series entrelazadas — p. ej.
         un hogar con "Primary household salary" el 15 y "Second household income"
         el 20: juntas dan intervalos 5/25/5/25 y ninguna cadencia; separadas son dos
         nóminas mensuales limpias. Sin esta pasada, user_13 se queda sin ingreso y
         `safe` cae de 433.40 a 0.
    """
    groups: dict[tuple, list] = {}
    for ev, day, amt in history:
        if ev.direction not in ("debit", "credit"):
            continue
        groups.setdefault((ev.direction, ev.event_type, ev.category), []).append(
            (ev, day, amt))

    out: list[Series] = []
    for key, rows in groups.items():
        s = _build(key, rows, as_of)
        if s is not None:
            out.append(s)
            continue
        subs: dict[tuple, list] = {}
        for r in rows:
            subs.setdefault(key + (norm(r[0].description),), []).append(r)
        if len(subs) < 2:
            continue
        for skey, srows in subs.items():
            s = _build(skey, srows, as_of)
            if s is not None:
                out.append(s)
    return out


def occurrences(s: Series, start: date, end: date,
                as_of: date | None = None) -> list[date]:
    """Fechas proyectadas de la serie dentro de (start, end] — ver INCLUDE_DAY_ZERO."""
    days: list[date] = []
    saltar = 0
    if as_of is not None and params.SKIP_IF_RECENT_FRAC > 0:
        per = 30 if s.period_kind == "month" else s.period
        if (as_of - s.last_day).days < per * params.SKIP_IF_RECENT_FRAC:
            saltar = 1
    if s.period_kind == "month":
        cur = _clamp_day(s.last_day.year, s.last_day.month, s.period)
        if cur <= s.last_day:
            cur = add_months(cur, 1, s.period)
        guard = 0
        while cur <= end and guard < 200:
            if cur >= start:
                days.append(cur)
            cur = add_months(cur, 1, s.period)
            guard += 1
        if params.COUNT_MODE != "fecha_real":
            days = days[:max(1, params.HORIZON // 30)]
        days = days[saltar:]
    else:
        cur = s.last_day + timedelta(days=s.period)
        guard = 0
        while cur <= end and guard < 400:
            if cur >= start:
                days.append(cur)
            cur = cur + timedelta(days=s.period)
            guard += 1
        if params.COUNT_MODE != "fecha_real":
            days = days[:max(1, params.HORIZON // s.period)]
        days = days[saltar:]
    return days
