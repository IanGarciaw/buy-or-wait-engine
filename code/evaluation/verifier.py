"""A4 · VERIFICADOR INDEPENDIENTE.

Segunda implementación del forecast de 90 días, escrita desde el enunciado.
NO importa `finance/` ni `decision/` a propósito: si compartiera su lógica
compartiría sus puntos ciegos, y un falso verde del motor sería un falso verde mío.
Sólo importa `contracts` (tipos) y `loader` (lectura de CSV).

El verificador NO lee texto de mensajes ni pixeles de imágenes (contracts.py, regla 4).
Puede recibir `facts` ya tipados (MessageFact/ImageFact, esquema cerrado) para no
quedarse ciego ante enmiendas; sin ellos marca "incertidumbre" y NO bloquea por
forecast, porque la discrepancia sería culpa de mis insumos, no del motor.

La autoridad NO es uniforme por campo. BLOCK sólo por invariantes ESTRUCTURALES
(los que se leen del dataset). Todo lo que derive de MI forecast —piso de saldo,
nivel de amount_safe_to_pay, earliest— sale como WARN con su delta: se mide
siempre, no veta nunca. Ver MODEL_TAGS.


Tres desenlaces:
  PASS   — todo en verde.
  REPAIR — sólo correcciones inequívocas (formato, redondeo, cap de safe,
           suma exacta de los dos pagos del partial). Nunca reinterpreta el problema.
  BLOCK  — inconsistencia sustantiva. Verdict.decision trae la alternativa
           conservadora (not_recommended, plan "none") para que decida el director.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

_CODE = Path(__file__).resolve().parent.parent
if str(_CODE) not in sys.path:
    sys.path.insert(0, str(_CODE))

from contracts import (AFFORDABILITY, BLOCK, CAN_REDUCE, CAN_STOP, HORIZON_DAYS,
                       METHODS, PASS, REPAIR, Dataset, Decision, Event,
                       ImageFact, MessageFact, Payment, PaymentOption, Profile,
                       Request, SpendingChange, Verdict)
import loader

CENT = Decimal("0.01")
ZERO = Decimal(0)


# ═════════════════════════════════════════════════════════════════════════════
# 1. MI PROPIO MODELO DE CAJA — escrito desde el enunciado, no desde finance/
# ═════════════════════════════════════════════════════════════════════════════
#
# Reglas que aplico (enunciado §90-Day Safety Check y §6.3 de AGENTS.md):
#   · El saldo arranca en current_available_balance el request_date.
#   · Cuenta en el futuro: scheduled (en settlement_date), settled con
#     settlement_date futura, y pending SÓLO si es débito (se reserva).
#   · NO cuenta: cancelled, failed, unrealized, non_cash, ni crédito pending.
#   · Recurrencia sólo cuando la historia la respalda (>= MIN_OCCURRENCES).
#   · Gasto variable esencial se proyecta conservador (el mayor entre media y mediana).
#   · Ingreso recurrente se proyecta conservador (el menor entre media y última),
#     salvo que exista un ingreso confirmado futuro: ese fija el monto siguiente.
#   · amount en blanco NO es cero: es desconocido y contamina la confianza.

MIN_OCCURRENCES = 3        # menos de 3 no es una serie, es una anécdota
LOOKBACK_DAYS = 190        # ventana de historia para inferir cadencia
MAX_GAP_DAYS = 45          # más espaciado que esto no se proyecta a 90 días
MIN_GAP_DAYS = 3


@dataclass(frozen=True)
class Series:
    """Una serie recurrente inferida de la historia."""
    key: tuple
    category: str
    direction: str            # debit | credit
    flexibility: str
    amount: Decimal           # monto proyectado por ocurrencia (moneda del hogar)
    gap_days: int
    monthly: bool             # True => paso de mes a mes conservando el día
    last_seen: date
    member_ids: frozenset[str]
    minimum_allowed: Decimal | None


@dataclass
class CashModel:
    profile: Profile
    start: date
    end: date
    fixed: list[tuple[date, Decimal, str]] = field(default_factory=list)
    series: list[Series] = field(default_factory=list)
    unknown_event_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def has_unknowns(self) -> bool:
        return bool(self.unknown_event_ids)


def _eff_date(e: Event) -> date | None:
    return e.settlement_date or e.event_date


def _counts_in_future(e: Event) -> bool:
    """¿Este evento mueve caja en el futuro?"""
    if e.direction == "non_cash":
        return False
    if e.status in ("cancelled", "failed", "unrealized"):
        return False
    if e.status == "pending" and e.direction == "credit":
        return False          # pendiente a favor NO se cuenta
    return True


def _median(xs: list[Decimal]) -> Decimal:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return ZERO
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _mean(xs: list[Decimal]) -> Decimal:
    return sum(xs) / Decimal(len(xs)) if xs else ZERO


def _add_month(d: date, k: int = 1) -> date:
    m = d.month - 1 + k
    y = d.year + m // 12
    m = m % 12 + 1
    # Conserva el día; si el mes es corto, cae al último día.
    for day in range(d.day, 27, -1):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, min(d.day, 28))


def _apply_facts(events: list[Event], facts) -> tuple[list[Event], list[str]]:
    """Aplica hechos YA TIPADOS (esquema cerrado). Nunca texto libre."""
    notes: list[str] = []
    if not facts:
        return events, notes
    by_id = {e.event_id: e for e in events}
    drop: set[str] = set()
    for f in facts:
        if isinstance(f, ImageFact):
            e = by_id.get(f.event_id)
            if e is not None and e.amount is None and f.amount is not None:
                by_id[f.event_id] = replace(e, amount=f.amount)
                notes.append(f"image_fact:{f.event_id}={f.amount}")
            continue
        if not isinstance(f, MessageFact):
            continue
        tid = f.target_event_id
        e = by_id.get(tid) if tid else None
        if f.kind == "cancellation" and e is not None:
            drop.add(e.event_id)
            notes.append(f"fact_cancel:{e.event_id}")
        elif f.kind == "amount_amendment" and e is not None and f.amount is not None:
            by_id[e.event_id] = replace(e, amount=f.amount)
            notes.append(f"fact_amend:{e.event_id}={f.amount}")
        elif f.kind == "delay" and e is not None and f.effective_date:
            by_id[e.event_id] = replace(e, settlement_date=f.effective_date)
            notes.append(f"fact_delay:{e.event_id}->{f.effective_date}")
        elif f.kind in ("not_yet_cash", "duplicate_notice") and e is not None:
            drop.add(e.event_id)
            notes.append(f"fact_{f.kind}:{e.event_id}")
        elif f.kind in ("income_change", "income_date_change", "income_ended"):
            notes.append(f"fact_{f.kind}")   # se resuelve al construir las series
    return [by_id[k] for k in by_id if k not in drop], notes


def build_model(ds: Dataset, user_id: str, start: date,
                horizon_days: int = HORIZON_DAYS, facts=None) -> CashModel:
    prof = ds.profiles[user_id]
    end = start + timedelta(days=horizon_days)
    events = list(ds.events_by_user.get(user_id, ()))
    events, notes = _apply_facts(events, facts)

    unknown = tuple(sorted(e.event_id for e in events if e.amount is None
                           and _counts_in_future(e)))

    def home(amount: Decimal, e: Event, on: date) -> Decimal:
        if e.currency == prof.home_currency:
            return amount
        return loader.convert(amount, e.currency, prof.home_currency, on, ds.rates)

    fixed: list[tuple[date, Decimal, str]] = []
    hist: dict[tuple, list[Event]] = {}

    for e in events:
        d = _eff_date(e)
        if d is None:
            continue
        if d > start:
            if not _counts_in_future(e) or e.amount is None:
                continue
            amt = home(e.amount, e, d)
            sign = amt if e.direction == "credit" else -amt
            fixed.append((d, sign, e.event_id))
        else:
            # historia: sólo lo que realmente ocurrió sirve para inferir cadencia
            if e.status != "settled" or e.amount is None or e.direction == "non_cash":
                continue
            if d < start - timedelta(days=LOOKBACK_DAYS):
                continue
            key = (e.event_type, e.category, e.direction, e.flexibility)
            hist.setdefault(key, []).append(e)

    # Ingresos futuros ya confirmados: fijan el monto de las proyecciones siguientes.
    confirmed_income: dict[tuple, tuple[date, Decimal]] = {}
    for e in events:
        d = _eff_date(e)
        if d and d > start and e.direction == "credit" and e.amount is not None \
                and _counts_in_future(e):
            key = (e.event_type, e.category, e.direction, e.flexibility)
            amt = home(e.amount, e, d)
            prev = confirmed_income.get(key)
            if prev is None or d < prev[0]:
                confirmed_income[key] = (d, amt)

    series: list[Series] = []
    for key, evs in hist.items():
        evs.sort(key=lambda x: (_eff_date(x), x.event_id))
        if len(evs) < MIN_OCCURRENCES:
            continue
        days = [_eff_date(x) for x in evs]
        gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days > 0]
        if len(gaps) < MIN_OCCURRENCES - 1:
            continue
        g = int(_median([Decimal(x) for x in gaps]))
        if g < MIN_GAP_DAYS or g > MAX_GAP_DAYS:
            continue
        amounts = [home(x.amount, x, _eff_date(x)) for x in evs[-3:]]
        conf = confirmed_income.get(key)
        if key[2] == "debit":
            amt = max(_mean(amounts), _median(amounts))     # conservador: gasta más
        else:
            # "No inventes ingreso no respaldado": un historial de sueldos NO prueba
            # que el sueldo siga. Sólo proyecto ingreso si hay uno CONFIRMADO a
            # futuro que ancle la serie (el dataset marca el último con
            # 'Final employer payroll' y sin fila scheduled: ahí el ingreso murió).
            if conf is None and "final" in (evs[-1].description or "").lower():
                continue      # 'Final employer payroll' + sin fila futura = se acabó
            amt = min(_mean(amounts), amounts[-1])          # conservador: ingresa menos
        if conf is not None:
            amt = conf[1]                                    # el confirmado manda
        monthly = 26 <= g <= 32
        series.append(Series(
            key=key, category=key[1], direction=key[2], flexibility=key[3],
            amount=amt.quantize(CENT), gap_days=g, monthly=monthly,
            last_seen=days[-1] if conf is None else conf[0],
            member_ids=frozenset(x.event_id for x in evs),
            minimum_allowed=next((x.minimum_allowed_amount for x in reversed(evs)
                                  if x.minimum_allowed_amount is not None), None),
        ))

    # Un ingreso confirmado a futuro no es un evento suelto: es la próxima
    # ocurrencia de una nómina que sigue. Si la historia no alcanzó para inferir la
    # serie (p. ej. el usuario acaba de entrar al empleo), la anclo en el confirmado
    # y la proyecto mensual. Sin esto el horizonte de 90 días tendría un solo sueldo
    # y TODO usuario saldría insolvente — el ground truth demuestra lo contrario.
    have = {s.key for s in series}
    for key, (d0, amt) in confirmed_income.items():
        if key in have:
            continue
        series.append(Series(
            key=key, category=key[1], direction=key[2], flexibility=key[3],
            amount=amt.quantize(CENT), gap_days=30, monthly=True, last_seen=d0,
            member_ids=frozenset(), minimum_allowed=None,
        ))
        notes.append(f"income_anchor:{key[1]}@{d0}={amt.quantize(CENT)}")

    return CashModel(profile=prof, start=start, end=end, fixed=fixed,
                     series=series, unknown_event_ids=unknown,
                     notes=tuple(notes))


def _series_dates(s: Series, start: date, end: date) -> list[date]:
    out: list[date] = []
    d = s.last_seen
    guard = 0
    while guard < 400:
        guard += 1
        d = _add_month(d) if s.monthly else d + timedelta(days=s.gap_days)
        if d > end:
            break
        if d > start:
            out.append(d)
    return out


def _deltas(model: CashModel, changes: tuple[SpendingChange, ...] = ()) -> dict:
    """Movimientos día a día, ya aplicados los cambios de gasto."""
    stop_ids = {c.event_id for c in changes if c.kind == "stop"}
    red = {c.event_id: c.new_amount for c in changes if c.kind == "reduce_to"}
    per_day: dict[date, Decimal] = {}
    for d, amt, _eid in model.fixed:
        eid = _eid
        if eid in stop_ids:
            continue
        if eid in red and red[eid] is not None and amt < 0:
            amt = -min(-amt, red[eid])
        per_day[d] = per_day.get(d, ZERO) + amt
    for s in model.series:
        if s.member_ids & stop_ids:
            continue
        amt = s.amount
        hit = s.member_ids & set(red)
        if hit:
            new = min(x for x in (red[i] for i in hit) if x is not None)
            amt = min(amt, new)
        sign = amt if s.direction == "credit" else -amt
        for d in _series_dates(s, model.start, model.end):
            per_day[d] = per_day.get(d, ZERO) + sign
    return per_day


def trough(model: CashModel, changes: tuple[SpendingChange, ...] = (),
           payments: tuple[Payment, ...] = ()) -> tuple[Decimal, date]:
    """Punto más bajo del saldo en el horizonte, con plan y cambios aplicados."""
    per_day = dict(_deltas(model, changes))
    for p in payments:
        if model.start <= p.day <= model.end:
            per_day[p.day] = per_day.get(p.day, ZERO) - p.amount
    bal = model.profile.current_available_balance
    lo, lo_day = bal, model.start
    d = model.start
    while d <= model.end:
        bal += per_day.get(d, ZERO)
        if bal < lo:
            lo, lo_day = bal, d
        d += timedelta(days=1)
    return lo, lo_day


def safe_today(model: CashModel, requested: Decimal,
               changes: tuple[SpendingChange, ...] = ()) -> Decimal:
    """Lo máximo pagable HOY sin romper el mínimo en 90 días, capado al request."""
    lo, _ = trough(model, changes)
    head = lo - model.profile.minimum_balance_to_keep
    if head <= 0:
        return ZERO
    return min(head, requested).quantize(CENT)


def earliest_full(model: CashModel, requested: Decimal,
                  changes: tuple[SpendingChange, ...] = ()) -> date | None:
    """Primer día del horizonte en que el pago completo pasa la prueba de 90 días."""
    d = model.start
    while d <= model.end:
        lo, _ = trough(model, changes, (Payment(d, requested),))
        if lo >= model.profile.minimum_balance_to_keep:
            return d
        d += timedelta(days=1)
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 2. POLÍTICA
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# DOS CLASES DE INVARIANTE. El veredicto lo decide la CLASE, no un umbral.
#
#   ESTRUCTURAL  -> puede BLOQUEAR.
#       Formato, orden, rangos, coincidencia exacta con una PaymentOption,
#       elegibilidad del método, max_installment_months, flexibilidad y categorías,
#       exclusión de stop/reduce. Se leen del dataset y de la propia decisión.
#       Aquí mi veredicto es mejor que el del motor POR CONSTRUCCIÓN: no opino.
#
#   DE MODELO    -> sólo AVISA. Nunca bloquea.
#       Todo lo que sale de MI forecast: piso de saldo, nivel de amount_safe_to_pay,
#       mi earliest. Ahí compito con el motor usando un modelo que hoy es peor
#       (5/25 dentro de ±1% contra el ground truth, medido). La discrepancia es
#       información para dirigir el trabajo, no autoridad para tumbar una fila.
#       Se calcula SIEMPRE y se reporta SIEMPRE, con facts y sin ellos.
#
# Lo aprendido el 12-sep: A0 cableó los facts de A3 y el piso empezó a vetar.
# STATUS -3, METHOD -3, PLAN -2, EARLIEST -2, y 38 de 250 filas degradadas a la
# alternativa conservadora. Eran falsos rojos. El defecto no era el umbral: era
# darle autoridad de veto a un campo donde no la tengo.
# ─────────────────────────────────────────────────────────────────────────────

MODEL_TAGS = ("floor", "floor_abs", "safe", "earliest")


def _tag_of(incident: str) -> str:
    """Etiqueta EXACTA del incidente: 'safe_vs_status' no es 'safe'."""
    if "[" not in incident or "]" not in incident:
        return ""
    return incident.split("[", 1)[1].split("]", 1)[0]


@dataclass(frozen=True)
class Policy:
    horizon_days: int = HORIZON_DAYS
    # Tolerancia del piso: un céntimo de diferencia es ruido de redondeo del motor,
    # no una violación. No es un umbral de negocio inventado: es la unidad mínima
    # representable de la moneda en el CSV (2 decimales).
    floor_tolerance: Decimal = CENT


DEFAULT_POLICY = Policy()


# ═════════════════════════════════════════════════════════════════════════════
# 3. VERIFICACIÓN
# ═════════════════════════════════════════════════════════════════════════════

def _q(x: Decimal) -> Decimal:
    return Decimal(x).quantize(CENT)


def _option_dates(o: PaymentOption) -> list[date]:
    n = max(1, o.number_of_payments)
    step = o.payment_frequency_days or 0
    return [o.first_payment_date + timedelta(days=step * k) for k in range(n)]


def _matches_option(payments: tuple[Payment, ...], o: PaymentOption) -> bool:
    days = _option_dates(o)
    if len(payments) != len(days):
        return False
    for p, d in zip(payments, days):
        if p.day != d or _q(p.amount) != _q(o.payment_amount):
            return False
    return True


def _conservative(d: Decision, requested: Decimal) -> Decision:
    safe = d.amount_safe_to_pay
    if safe is None or safe < 0:
        safe = ZERO
    safe = min(_q(safe), _q(requested))
    return Decision(
        request_id=d.request_id, amount_safe_to_pay=safe,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payments=(), earliest_date_for_full_payment=None, spending_changes=(),
        decision_explanation=("Verificación independiente bloqueó la recomendación; "
                              "se devuelve la alternativa conservadora."),
        chosen_option_id=None, trace=d.trace, confidence=0.0,
    )


def verify(decision: Decision, req: Request, ds: Dataset,
           facts=None, policy: Policy = DEFAULT_POLICY) -> Verdict:
    """PASS | REPAIR | BLOCK para una decisión, con re-simulación independiente."""
    inc: list[str] = []
    blocks: list[str] = []
    d = decision
    prof = ds.profiles.get(req.user_id)
    if prof is None:
        return Verdict(BLOCK, _conservative(d, req.requested_amount),
                       (f"BLOCK[no_profile] usuario {req.user_id} sin perfil",))

    requested = _q(req.requested_amount)
    events = {e.event_id: e for e in ds.events_by_user.get(req.user_id, ())}
    options = ds.options_by_request.get(req.request_id, ())

    # ── 3.1 Tipos y formato (REPARABLE) ─────────────────────────────────────
    payments = list(d.payments or ())
    fixed_payments = []
    for p in payments:
        day, amt = p.day, p.amount
        if isinstance(day, str):
            try:
                day = date.fromisoformat(day[:10])
                inc.append(f"REPAIR[date_format] pago '{p.day}' -> {day.isoformat()}")
            except ValueError:
                blocks.append(f"BLOCK[date_format] fecha de pago ilegible: {p.day!r}")
                day = None
        if day is not None and _q(amt) != Decimal(amt):
            inc.append(f"REPAIR[rounding] pago {day}: {amt} -> {_q(amt)}")
        fixed_payments.append(Payment(day, _q(amt)) if day is not None else p)
    if not blocks:
        payments = fixed_payments

    earliest = d.earliest_date_for_full_payment
    if isinstance(earliest, str):
        try:
            earliest = date.fromisoformat(earliest[:10]) if earliest.strip() else None
            inc.append("REPAIR[date_format] earliest_date_for_full_payment normalizada")
        except ValueError:
            blocks.append("BLOCK[date_format] earliest_date_for_full_payment ilegible")
            earliest = None

    safe = d.amount_safe_to_pay
    if safe is None:
        blocks.append("BLOCK[safe_none] amount_safe_to_pay ausente")
        safe = ZERO
    if _q(safe) != Decimal(safe):
        inc.append(f"REPAIR[rounding] amount_safe_to_pay {safe} -> {_q(safe)}")
    safe = _q(safe)
    if safe < 0:
        inc.append(f"REPAIR[safe_range] amount_safe_to_pay {safe} < 0 -> 0")
        safe = ZERO
    if safe > requested:
        inc.append(f"REPAIR[safe_range] amount_safe_to_pay {safe} > "
                   f"requested {requested} -> capado")
        safe = requested

    # ── 3.2 Dominio ─────────────────────────────────────────────────────────
    if d.affordability_status not in AFFORDABILITY:
        blocks.append(f"BLOCK[status_domain] status inválido: "
                      f"{d.affordability_status!r}")
    if d.recommended_payment_method not in METHODS:
        blocks.append(f"BLOCK[method_domain] método inválido: "
                      f"{d.recommended_payment_method!r}")

    # ── 3.3 Orden cronológico ───────────────────────────────────────────────
    days = [p.day for p in payments if isinstance(p.day, date)]
    if days != sorted(days):
        blocks.append("BLOCK[plan_order] el plan no está en orden cronológico")
    if any(isinstance(p.amount, Decimal) and p.amount <= 0 for p in payments):
        blocks.append("BLOCK[plan_amount] hay un pago <= 0 en el plan")

    method = d.recommended_payment_method
    status = d.affordability_status
    changes = tuple(d.spending_changes or ())

    # ── 3.4 plan "none" sólo sin pago recomendado ───────────────────────────
    if method == "not_recommended" and payments:
        blocks.append("BLOCK[plan_vs_method] not_recommended con plan de pagos")
    if method in ("full_payment", "partial_payment", "installments", "wait") \
            and not payments:
        blocks.append(f"BLOCK[plan_vs_method] {method} sin plan de pagos")
    if method == "not_recommended":
        if status != "not_affordable":
            blocks.append(f"BLOCK[status_vs_method] not_recommended con "
                          f"status {status}")
        if changes:
            blocks.append("BLOCK[changes_vs_method] not_recommended con cambios "
                          "de gasto")

    # ── 3.5 Elegibilidad del método ─────────────────────────────────────────
    considered = set(prof.methods_considered)
    if method in ("full_payment", "partial_payment", "installments") \
            and method not in considered:
        blocks.append(f"BLOCK[method_eligibility] {method} no está en "
                      f"payment_methods_user_will_consider={sorted(considered)}")
    if method == "wait" and "full_payment" not in considered:
        blocks.append("BLOCK[method_eligibility] 'wait' exige que el usuario "
                      "acepte full_payment")

    # ── 3.6 affordable_now ⇒ earliest == request_date ───────────────────────
    if status == "affordable_now":
        if earliest != req.request_date:
            blocks.append(f"BLOCK[earliest_vs_now] affordable_now con earliest="
                          f"{earliest} != request_date {req.request_date}")
        if method != "full_payment":
            blocks.append(f"BLOCK[status_vs_method] affordable_now con "
                          f"método {method}")
        if changes:
            blocks.append("BLOCK[changes_vs_status] affordable_now no puede exigir "
                          "cambios de gasto")
        if safe != requested:
            blocks.append(f"BLOCK[safe_vs_status] affordable_now con safe={safe} "
                          f"!= requested={requested}")

    # ── 3.7 full_payment ────────────────────────────────────────────────────
    if method == "full_payment":
        if len(payments) != 1:
            blocks.append(f"BLOCK[plan_shape] full_payment con {len(payments)} pagos")
        elif payments[0].day != req.request_date or payments[0].amount != requested:
            blocks.append(f"BLOCK[plan_shape] full_payment debe ser "
                          f"{req.request_date}:{requested}, es "
                          f"{payments[0].day}:{payments[0].amount}")
        if status not in ("affordable_now", "affordable_with_plan"):
            blocks.append(f"BLOCK[status_vs_method] full_payment con status {status}")
        if status == "affordable_with_plan" and not changes:
            blocks.append("BLOCK[status_vs_method] full_payment + "
                          "affordable_with_plan sin cambios de gasto que lo expliquen")

    # ── 3.8 partial_payment ─────────────────────────────────────────────────
    if method == "partial_payment":
        if status != "affordable_with_plan":
            blocks.append(f"BLOCK[status_vs_method] partial_payment con "
                          f"status {status}")
        if not req.allows_partial_payment:
            blocks.append("BLOCK[partial_not_allowed] el request no admite pago parcial")
        if not (ZERO < safe < requested):
            blocks.append(f"BLOCK[partial_range] partial exige 0 < safe < requested "
                          f"(safe={safe}, requested={requested})")
        if len(payments) != 2:
            blocks.append(f"BLOCK[plan_shape] partial_payment con "
                          f"{len(payments)} pagos (deben ser 2)")
        else:
            p1, p2 = payments
            if p1.day != req.request_date:
                blocks.append(f"BLOCK[plan_shape] 1er pago parcial en {p1.day}, "
                              f"debe ser request_date {req.request_date}")
            if p1.amount != safe:
                blocks.append(f"BLOCK[plan_shape] 1er pago {p1.amount} != "
                              f"amount_safe_to_pay {safe}")
            if earliest is not None and p2.day != earliest:
                blocks.append(f"BLOCK[plan_shape] 2º pago en {p2.day} != "
                              f"earliest {earliest}")
            if isinstance(p2.day, date) and p2.day > req.desired_completion_date:
                blocks.append(f"BLOCK[deadline] 2º pago {p2.day} después de "
                              f"desired_completion_date {req.desired_completion_date}")
            total = _q(p1.amount + p2.amount)
            if total != requested:
                gap = requested - total
                inc.append(f"REPAIR[partial_sum] los dos pagos suman {total}, "
                           f"ajusto el 2º en {gap} para sumar {requested}")
                payments = [p1, Payment(p2.day, _q(p2.amount + gap))]

    # ── 3.9 installments ────────────────────────────────────────────────────
    if method == "installments":
        if status != "affordable_with_plan":
            blocks.append(f"BLOCK[status_vs_method] installments con status {status}")
        if prof.max_installment_months is None:
            blocks.append("BLOCK[installments_not_accepted] max_installment_months "
                          "vacío: el usuario no acepta plazos")
        hits = [o for o in options if _matches_option(tuple(payments), o)]
        if not hits:
            blocks.append("BLOCK[option_mismatch] el plan no coincide exactamente con "
                          "ninguna PaymentOption de request_payment_options.csv")
        else:
            o = hits[0]
            if o.payment_method != "installments":
                blocks.append(f"BLOCK[option_mismatch] {o.payment_option_id} no es "
                              f"una opción de installments")
            if prof.max_installment_months is not None and \
                    o.number_of_payments > prof.max_installment_months:
                blocks.append(f"BLOCK[max_installments] {o.number_of_payments} pagos > "
                              f"max_installment_months={prof.max_installment_months}")
            if d.chosen_option_id and d.chosen_option_id != o.payment_option_id:
                inc.append(f"WARN[option_id] chosen_option_id={d.chosen_option_id} "
                           f"pero el plan coincide con {o.payment_option_id}")

    # ── 3.10 wait ───────────────────────────────────────────────────────────
    if method == "wait":
        if status != "affordable_later":
            blocks.append(f"BLOCK[status_vs_method] wait con status {status}")
        if len(payments) != 1:
            blocks.append(f"BLOCK[plan_shape] wait con {len(payments)} pagos")
        else:
            p = payments[0]
            if p.amount != requested:
                blocks.append(f"BLOCK[plan_shape] wait debe pagar el total "
                              f"{requested}, paga {p.amount}")
            if earliest is not None and p.day != earliest:
                blocks.append(f"BLOCK[plan_shape] wait paga en {p.day} != "
                              f"earliest {earliest}")
            if isinstance(p.day, date) and p.day <= req.request_date:
                blocks.append(f"BLOCK[plan_shape] wait con pago en {p.day} <= "
                              f"request_date {req.request_date}")
            if isinstance(p.day, date) and p.day > req.desired_completion_date:
                inc.append(f"WARN[deadline] wait completa en {p.day}, después de "
                           f"desired_completion_date {req.desired_completion_date}")
        if changes:
            inc.append("WARN[changes_vs_method] wait con cambios de gasto")

    # ── 3.11 not_affordable ─────────────────────────────────────────────────
    if status == "not_affordable" and earliest is not None:
        inc.append(f"WARN[earliest_vs_status] not_affordable con earliest={earliest}; "
                   "el enunciado lo deja vacío cuando el total no llega a ser seguro")

    # ── 3.12 Cambios de gasto ───────────────────────────────────────────────
    if len(changes) > 3:
        blocks.append(f"BLOCK[changes_count] {len(changes)} cambios de gasto (máx 3)")
    seen: set[str] = set()
    for c in changes:
        if c.kind not in ("stop", "reduce_to"):
            blocks.append(f"BLOCK[change_kind] tipo de cambio inválido: {c.kind!r}")
            continue
        if c.event_id in seen:
            blocks.append(f"BLOCK[change_conflict] {c.event_id} aparece dos veces en "
                          "spending_changes (stop y reduce_to son excluyentes)")
        seen.add(c.event_id)
        e = events.get(c.event_id)
        if e is None:
            blocks.append(f"BLOCK[change_unknown_event] {c.event_id} no existe para "
                          f"{req.user_id}")
            continue
        if e.direction != "debit":
            blocks.append(f"BLOCK[change_not_expense] {c.event_id} no es un débito "
                          f"({e.direction})")
        if e.category in prof.protect:
            blocks.append(f"BLOCK[change_protected] {c.event_id} es de categoría "
                          f"protegida '{e.category}'")
        if c.kind == "stop":
            if e.flexibility not in CAN_STOP:
                blocks.append(f"BLOCK[change_flexibility] stop sobre {c.event_id} con "
                              f"flexibility='{e.flexibility}' (exige "
                              f"stoppable|reducible_or_stoppable)")
            if e.category not in prof.willing_stop:
                blocks.append(f"BLOCK[change_not_permitted] el usuario no acepta parar "
                              f"'{e.category}' (acepta {sorted(prof.willing_stop)})")
        else:
            if e.flexibility not in CAN_REDUCE:
                blocks.append(f"BLOCK[change_flexibility] reduce_to sobre {c.event_id} "
                              f"con flexibility='{e.flexibility}' (exige "
                              f"reducible|reducible_or_stoppable)")
            if e.category not in prof.willing_reduce:
                blocks.append(f"BLOCK[change_not_permitted] el usuario no acepta "
                              f"reducir '{e.category}' (acepta "
                              f"{sorted(prof.willing_reduce)})")
            if c.new_amount is None:
                blocks.append(f"BLOCK[change_amount] reduce_to sin monto en "
                              f"{c.event_id}")
            else:
                if c.new_amount < 0:
                    blocks.append(f"BLOCK[change_amount] reduce_to negativo en "
                                  f"{c.event_id}")
                if e.minimum_allowed_amount is not None and \
                        c.new_amount < e.minimum_allowed_amount:
                    blocks.append(f"BLOCK[change_below_minimum] reduce_to "
                                  f"{c.new_amount} < minimum_allowed_amount "
                                  f"{e.minimum_allowed_amount} en {c.event_id}")
                if e.amount is not None and c.new_amount >= e.amount:
                    inc.append(f"WARN[change_no_effect] reduce_to {c.new_amount} >= "
                               f"monto actual {e.amount} en {c.event_id}")

    # ── 3.13 Re-simulación independiente ────────────────────────────────────
    model = build_model(ds, req.user_id, req.request_date,
                        horizon_days=policy.horizon_days, facts=facts)
    valid_changes = tuple(c for c in changes if c.event_id in events)
    plan = tuple(p for p in payments if isinstance(p.day, date))
    minimum = prof.minimum_balance_to_keep
    lo_base, base_day = trough(model)                     # sin plan, sin cambios
    lo, lo_day = trough(model, valid_changes, plan)        # con plan y cambios
    my_safe = safe_today(model, requested)
    my_earliest = earliest_full(model, requested)

    inc.append(f"INFO[model] mi piso sin plan={_q(lo_base)} el {base_day} · "
               f"con plan={_q(lo)} el {lo_day} (mínimo {minimum}) · "
               f"mi safe={my_safe} · mi earliest={my_earliest} · "
               f"series={len(model.series)} · desconocidos={len(model.unknown_event_ids)}")

    # ¿Mis insumos son los mismos que los del motor? Si el usuario tiene mensajes
    # o imágenes que yo me niego a leer (o montos que sólo salen de una imagen),
    # una discrepancia es culpa de MI ceguera, no defecto del motor: se reporta,
    # no se bloquea. Con `facts` tipados de A3 recupero la vista y vuelvo a morder.
    has_evidence = bool(ds.messages_by_user.get(req.user_id)) or \
        any(i.user_id == req.user_id for i in ds.images) or model.has_unknowns
    confident = (facts is not None and not model.has_unknowns) or not has_evidence

    # ── Piso DIFERENCIAL ─────────────────────────────────────────────────────
    # Mi modelo y el del motor NO coinciden en NIVEL (lo medí: hasta 21x sobre los
    # 25 samples). Un chequeo absoluto sería un generador de falsos rojos.
    # Así que anclo MI curva en la afirmación del propio motor —`amount_safe_to_pay`
    # es su declaración de cuánto cabe hoy— y verifico la FORMA: si el plan
    # pide más de lo declarado hoy, o pide en un día en que mi curva no da,
    # el error de nivel se cancela y lo que queda es un defecto real del plan.
    head_mine = lo_base - minimum
    head_engine = safe if safe < requested else max(safe, head_mine)
    delta = head_engine - head_mine
    adjusted = lo + delta
    floor_breach = adjusted < minimum - policy.floor_tolerance
    # Señal, no veredicto. Se emite con facts y sin ellos.
    par = ("insumos equivalentes a los del motor" if confident
           else f"mi modelo NO leyó la evidencia de {req.user_id}")
    if floor_breach:
        inc.append(f"WARN[floor] piso roto (forma del plan): anclado en el propio "
                   f"amount_safe_to_pay={safe} del motor, el plan deja el saldo en "
                   f"{_q(adjusted)} el {lo_day}, por debajo de "
                   f"minimum_balance_to_keep={minimum} "
                   f"(faltan {_q(minimum - adjusted)}) · {par}")
    if lo < minimum - policy.floor_tolerance:
        inc.append(f"WARN[floor_abs] piso roto (nivel de mi modelo): saldo {_q(lo)} "
                   f"el {lo_day} < mínimo {minimum} · {par}")

    if my_safe != safe:
        inc.append(f"WARN[safe] motor={safe} vs modelo independiente={my_safe} "
                   f"(delta={_q(safe - my_safe)}) · {par}")
    if my_earliest != earliest:
        inc.append(f"WARN[earliest] motor={earliest} vs modelo "
                   f"independiente={my_earliest} · {par}")

    # ── 3.14 Veredicto ──────────────────────────────────────────────────────
    # Compuerta contra mí mismo: ningún invariante DE MODELO puede llegar a BLOCK.
    # Si una edición futura lo intenta, se degrada a aviso y se deja constancia,
    # en vez de convertirse en un falso rojo silencioso.
    leaked = [b for b in blocks if _tag_of(b) in MODEL_TAGS]
    if leaked:
        blocks = [b for b in blocks if _tag_of(b) not in MODEL_TAGS]
        for b in leaked:
            inc.append("WARN[leak] un invariante DE MODELO intentó bloquear y fue "
                       "degradado a aviso: " + b)

    if blocks:
        return Verdict(BLOCK, _conservative(d, requested), tuple(blocks + inc))

    repaired = replace(
        d, amount_safe_to_pay=safe, payments=tuple(payments),
        earliest_date_for_full_payment=earliest,
        spending_changes=tuple(changes),
    )
    if any(i.startswith("REPAIR") for i in inc):
        return Verdict(REPAIR, repaired, tuple(inc))
    return Verdict(PASS, repaired, tuple(inc))


# ═════════════════════════════════════════════════════════════════════════════
# 4. CALIBRACIÓN CONTRA EL CORPUS REAL — las 25 decisiones del ground truth
# ═════════════════════════════════════════════════════════════════════════════

def _parse_plan(s: str) -> tuple[Payment, ...]:
    s = (s or "").strip()
    if not s or s == "none":
        return ()
    out = []
    for part in s.split("|"):
        day, amt = part.split(":")
        out.append(Payment(date.fromisoformat(day.strip()), Decimal(amt.strip())))
    return tuple(out)


def _parse_changes(s: str) -> tuple[SpendingChange, ...]:
    s = (s or "").strip()
    if not s or s == "none":
        return ()
    out = []
    for part in s.split("|"):
        bits = part.split(":")
        if bits[0] == "stop":
            out.append(SpendingChange("stop", bits[1]))
        else:
            out.append(SpendingChange("reduce_to", bits[1], Decimal(bits[2])))
    return tuple(out)


def ground_truth_decisions() -> list[Decision]:
    """Las 25 decisiones RESUELTAS del sample, como Decision. El corpus real."""
    out = []
    for r in loader.load_samples():
        e = (r.get("earliest_date_for_full_payment") or "").strip()
        out.append(Decision(
            request_id=r["request_id"],
            amount_safe_to_pay=Decimal(r["amount_safe_to_pay"]),
            affordability_status=r["affordability_status"],
            recommended_payment_method=r["recommended_payment_method"],
            payments=_parse_plan(r["payment_plan"]),
            earliest_date_for_full_payment=date.fromisoformat(e) if e else None,
            spending_changes=_parse_changes(r["spending_changes_needed"]),
            decision_explanation=r.get("decision_explanation", ""),
        ))
    return out


def calibrate(policy: Policy = DEFAULT_POLICY, verbose: bool = True) -> dict:
    """Un verificador que sólo se ha visto verde no ha demostrado saber ponerse rojo.
    Aquí mido lo contrario: que NO se ponga rojo sobre el ground truth.
    Cualquier BLOCK sobre estas 25 decisiones es un defecto MÍO."""
    ds = loader.load()
    reqs = {r.request_id: r for r in loader.sample_requests()}
    res = {"PASS": 0, "REPAIR": 0, "BLOCK": 0}
    false_blocks, safe_deltas, early_hits = [], [], 0
    for d in ground_truth_decisions():
        v = verify(d, reqs[d.request_id], ds, policy=policy)
        res[v.outcome] += 1
        if v.outcome == BLOCK:
            false_blocks.append((d.request_id,
                                 [i for i in v.incidents if i.startswith("BLOCK")]))
        mine = next((i for i in v.incidents if i.startswith("INFO[model]")), "")
        got = None
        for tok in mine.split("·"):
            if "mi safe=" in tok:
                got = Decimal(tok.split("mi safe=")[1].strip())
        if got is not None:
            exp = d.amount_safe_to_pay
            safe_deltas.append((d.request_id, exp, got))
        if not any(i.startswith("WARN[earliest]") for i in v.incidents):
            early_hits += 1
    if verbose:
        print("CALIBRACIÓN DEL VERIFICADOR contra las 25 decisiones del ground truth")
        print(f"  PASS={res['PASS']}  REPAIR={res['REPAIR']}  BLOCK={res['BLOCK']}")
        if false_blocks:
            print("  FALSOS BLOQUEOS (defecto del verificador, no del motor):")
            for rid, bs in false_blocks:
                for b in bs:
                    print(f"    {rid}: {b}")
        ok1 = sum(1 for _, e, g in safe_deltas
                  if (e == 0 and g == 0) or (e != 0 and abs(g - e) <= abs(e) / 100))
        print(f"  mi amount_safe_to_pay dentro de ±1% del ground truth: "
              f"{ok1}/{len(safe_deltas)}")
        print(f"  mi earliest_date_for_full_payment igual al ground truth: "
              f"{early_hits}/25")
        print("  (los deltas miden MI modelo, no el motor: son el margen de error "
               "con el que hay que leer mis WARN de forecast)")
    return {"outcomes": res, "false_blocks": false_blocks,
            "safe_deltas": safe_deltas, "earliest_hits": early_hits}


if __name__ == "__main__":
    calibrate()
