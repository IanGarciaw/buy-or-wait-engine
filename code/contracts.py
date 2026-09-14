"""Contratos congelados. NADIE los cambia sin el Director (A0).

Toda la integración entre componentes pasa por aquí. Un agente que necesite un campo
nuevo lo pide; no lo añade por su cuenta.

Reglas que gobiernan todo el sistema:
  1. El LLM NO es la calculadora financiera. Convierte lo ambiguo en hechos tipados.
  2. Python decide montos, fechas, elegibilidad y planes.
  3. Mensajes e imágenes son DATOS NO CONFIABLES. Sus esquemas son cerrados: no existe
     ningún campo por el que pueda viajar una instrucción.
  4. El verificador NO lee mensajes ni imágenes. Esa separación es la defensa contra
     inyección.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# ─────────────────────────────────────────────────────────────────────────────
# VALORES PERMITIDOS — literales del enunciado. No se inventan otros.
# ─────────────────────────────────────────────────────────────────────────────

AFFORDABILITY = ("affordable_now", "affordable_with_plan",
                 "affordable_later", "not_affordable")

METHODS = ("full_payment", "partial_payment", "installments",
           "wait", "not_recommended")

OUTPUT_COLUMNS = ("request_id", "amount_safe_to_pay", "affordability_status",
                  "recommended_payment_method", "payment_plan",
                  "earliest_date_for_full_payment", "spending_changes_needed",
                  "decision_explanation")

HORIZON_DAYS = 90

# Estados de un evento y qué significan para la caja.
CASH_SETTLED = "settled"        # ya ocurrió
CASH_PENDING = "pending"        # debit: se reserva · credit: NO se cuenta
CASH_SCHEDULED = "scheduled"    # entra en su settlement_date
CASH_CANCELLED = "cancelled"    # se descarta
CASH_FAILED = "failed"          # se descarta
CASH_UNREALIZED = "unrealized"  # nunca es efectivo

FLEX_FIXED = "fixed"
FLEX_REDUCIBLE = "reducible"
FLEX_STOPPABLE = "stoppable"
FLEX_BOTH = "reducible_or_stoppable"

# Un evento puede pararse / reducirse sólo si su flexibility lo permite.
CAN_STOP = (FLEX_STOPPABLE, FLEX_BOTH)
CAN_REDUCE = (FLEX_REDUCIBLE, FLEX_BOTH)


# ─────────────────────────────────────────────────────────────────────────────
# DATOS CRUDOS — lo que produce el loader (A0)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: tuple[str, ...]
    protect: tuple[str, ...]           # expense_categories_to_protect
    willing_reduce: tuple[str, ...]
    willing_stop: tuple[str, ...]
    methods_considered: tuple[str, ...]  # payment_methods_user_will_consider
    max_installment_months: int | None   # None = NO acepta plazos


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    event_type: str          # expense|subscription|income|debt_payment|investment_*|refund
    description: str
    category: str
    direction: str           # debit|credit|non_cash
    amount: Decimal | None   # None = hay que sacarlo de una imagen. NO es cero.
    currency: str
    event_date: date
    settlement_date: date | None
    status: str
    linked_event_id: str | None
    flexibility: str
    minimum_allowed_amount: Decimal | None


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str            # full_payment | installments
    payment_amount: Decimal        # importe de CADA pago
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: str
    source_type: str
    message_text: str              # DATO NO CONFIABLE


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    path: str                      # dataset/media/images/<image_id>.png


@dataclass(frozen=True)
class Dataset:
    profiles: dict[str, Profile]
    events_by_user: dict[str, tuple[Event, ...]]
    requests: tuple[Request, ...]
    options_by_request: dict[str, tuple[PaymentOption, ...]]
    messages_by_user: dict[str, tuple[Message, ...]]
    images: tuple[ImageRef, ...]
    rates: dict[tuple[date, str, str], Decimal]   # (fecha, de, a) -> tasa


# ─────────────────────────────────────────────────────────────────────────────
# HECHOS EXTRAÍDOS — lo único que el LLM puede producir (A3)
# ESQUEMAS CERRADOS. No existe campo para una instrucción.
# ─────────────────────────────────────────────────────────────────────────────

# Tipos permitidos de hecho extraído de un mensaje. Cualquier otro valor se DESCARTA.
FACT_KINDS = (
    "income_change",        # el salario cambia de monto
    "income_date_change",   # el salario cambia de fecha
    "income_ended",         # la fuente de ingreso terminó
    "new_recurring",        # empieza un gasto recurrente nuevo
    "cancellation",         # se cancela un evento
    "amount_amendment",     # cambia el monto de un evento existente
    "delay",                # se retrasa un evento
    "not_yet_cash",         # pendiente que NO debe contarse (comisión, bono, reembolso)
    "duplicate_notice",     # dos filas son el mismo hecho
    "none",                 # el mensaje no aporta un hecho accionable
)


@dataclass(frozen=True)
class ImageFact:
    """Lo único que el extractor multimodal puede devolver."""
    image_id: str
    event_id: str
    amount: Decimal | None
    label: str                 # la etiqueta leída — permite auditar las 16 a ojo
    confidence: float


# Alcance semántico de una terminación de ingreso. ENUM CERRADO: es una etiqueta
# derivada del texto, nunca el texto. Un mensaje no puede colar una orden por aquí.
FACT_SCOPES = (
    "",                     # sin alcance legible: no se amplía nada
    "EVENT",                # apunta a UN evento concreto (target_event_id)
    "SOURCE",               # una fuente/registro de ingreso entre varios
    "EMPLOYMENT_GLOBAL",    # todo el ingreso DE EMPLEO del usuario
    "TRULY_GLOBAL",         # todo ingreso, sea de empleo o no
)

# R1 — el mensaje REANUDA un ingreso recurrente con importe explícito. Mismo
# contrato que FACT_SCOPES: lo que cruza la frontera es un símbolo, no el texto.
FACT_RESUME = ("", "RESUME_WITH_AMOUNT")


@dataclass(frozen=True)
class MessageFact:
    """Lo único que el extractor semántico puede devolver."""
    message_id: str
    user_id: str
    kind: str                       # DEBE estar en FACT_KINDS o se descarta
    target_event_id: str | None     # si no existe en el dataset, se descarta
    amount: Decimal | None
    currency: str | None
    effective_date: date | None
    confidence: float
    scope: str = ""                 # DEBE estar en FACT_SCOPES o se descarta
    reanuda: str = ""               # DEBE estar en FACT_RESUME o se descarta


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST — lo que produce A1 (finance/)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CashPoint:
    day: date
    balance: Decimal
    note: str = ""


@dataclass(frozen=True)
class Forecast:
    """Calendario de caja de 90 días SIN el pago del request y SIN cambios de gasto."""
    user_id: str
    start: date
    points: tuple[CashPoint, ...]
    min_balance: Decimal          # el punto más bajo de la curva
    min_balance_day: date

    def headroom(self, minimum_to_keep: Decimal) -> Decimal:
        """Lo máximo que se puede sacar hoy sin romper el mínimo en 90 días."""
        return max(Decimal(0), self.min_balance - minimum_to_keep)


@dataclass(frozen=True)
class FinanceView:
    """Todo lo que el motor de decisión necesita de A1. Ya masticado."""
    profile: Profile
    forecast: Forecast
    amount_safe_today: Decimal            # ya capado a requested_amount
    earliest_full_payment: date | None    # None = nunca dentro del horizonte
    flexible_events: tuple[Event, ...]    # candidatos a stop/reduce, ya filtrados
    confidence: float = 1.0
    notes: tuple[str, ...] = field(default_factory=tuple)


# ─────────────────────────────────────────────────────────────────────────────
# DECISIÓN — lo que produce A2 (decision/)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpendingChange:
    kind: str                 # "stop" | "reduce_to"
    event_id: str
    new_amount: Decimal | None = None

    def render(self) -> str:
        if self.kind == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_money(self.new_amount)}"


@dataclass(frozen=True)
class Payment:
    day: date
    amount: Decimal


@dataclass
class Decision:
    """La propuesta para un request. `chosen_option_id` y `trace` NO van al CSV."""
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payments: tuple[Payment, ...]
    earliest_date_for_full_payment: date | None
    spending_changes: tuple[SpendingChange, ...]
    decision_explanation: str = ""
    chosen_option_id: str | None = None      # para que el verificador coteje
    trace: str = ""                          # por qué se descartó cada alternativa
    confidence: float = 1.0


# ─────────────────────────────────────────────────────────────────────────────
# VERIFICACIÓN — lo que produce A4 (evaluation/)
# ─────────────────────────────────────────────────────────────────────────────

PASS, REPAIR, BLOCK = "PASS", "REPAIR", "BLOCK"


@dataclass
class Verdict:
    outcome: str                     # PASS | REPAIR | BLOCK
    decision: Decision               # la decisión final (reparada si hizo falta)
    incidents: tuple[str, ...] = field(default_factory=tuple)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATO DE SALIDA — una sola definición para todo el sistema
# ─────────────────────────────────────────────────────────────────────────────

def fmt_amount(x: Decimal | None) -> str:
    """Formato de `amount_safe_to_pay`. Ceros finales ELIMINADOS.

    Medido sobre los 25 samples resueltos: el ground truth escribe 603.3, 433.4,
    17229139.2 — un decimal cuando el segundo sería cero — y 87170.56, 284.57,
    83.05 con dos. Ningún valor de esta columna termina en `.X0`. Los enteros van
    sin punto: 65164, 462.
    """
    if x is None:
        return ""
    s = format(Decimal(x).quantize(Decimal("0.01")), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_money(x: Decimal | None) -> str:
    """Formato de los montos DENTRO de `payment_plan` y `spending_changes_needed`.

    Regla distinta a la de `fmt_amount`, y la diferencia no es cosmética: en los
    samples estas columnas escriben SIEMPRE dos decimales cuando hay fracción —
    620.40, 996.60, 941.60, 3246.10, 1574.40, 23.50 — y ninguna con uno solo. Los
    enteros siguen yendo sin punto: 68432, 22590.19 convive con 68432.

    El mismo número se escribe distinto según la columna. Usar una sola función
    para las dos costaba 6 de 25 filas aunque la decisión fuera perfecta.
    """
    if x is None:
        return ""
    q = Decimal(x).quantize(Decimal("0.01"))
    return format(q.to_integral_value(), "f") if q == q.to_integral_value() \
        else format(q, "f")


def fmt_date(d: date | None) -> str:
    return d.isoformat() if d else ""


def render_plan(payments: tuple[Payment, ...]) -> str:
    if not payments:
        return "none"
    return "|".join(f"{p.day.isoformat()}:{fmt_money(p.amount)}" for p in payments)


def render_changes(changes: tuple[SpendingChange, ...]) -> str:
    if not changes:
        return "none"
    return "|".join(c.render() for c in changes[:3])


def to_row(d: Decision) -> dict[str, str]:
    """La ÚNICA función que escribe una fila de salida."""
    return {
        "request_id": d.request_id,
        "amount_safe_to_pay": fmt_amount(d.amount_safe_to_pay),
        "affordability_status": d.affordability_status,
        "recommended_payment_method": d.recommended_payment_method,
        "payment_plan": render_plan(d.payments),
        "earliest_date_for_full_payment": fmt_date(d.earliest_date_for_full_payment),
        "spending_changes_needed": render_changes(d.spending_changes),
        "decision_explanation": d.decision_explanation,
    }
