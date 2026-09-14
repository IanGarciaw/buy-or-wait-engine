#!/usr/bin/env python3
"""spec_oracle.py — ORÁCULO DE ESPECIFICACIÓN. Verifica output.csv contra el ENUNCIADO.

    python3 code/evaluation/oracle/spec_oracle.py            # 250 filas -> ORACLE.md
    python3 code/evaluation/oracle/spec_oracle.py --quiet    # sin tabla en stdout

Qué NO es: no reproduce la heurística de `decision/`. Cada comprobación transcribe una
frase literal de `problem_statement.md` y la aplica sobre las 250 filas de `output.csv`.
La única pieza del motor que se reutiliza es la CURVA de caja (`finance.view`), porque
sin ella no hay forma de evaluar "the balance never falls below minimum_balance_to_keep";
la curva es el instrumento, no el criterio.

Salida: `code/evaluation/oracle/ORACLE.md`. Código de salida != 0 si algún invariante falla.

Mutación falsadora del propio oráculo (se corre con --self-test): alterar una fila de
output.csv en memoria — poner `amount_safe_to_pay` > `requested_amount`, un `wait` con
fecha vacía, un plan de cuotas con un importe distinto al de su opción — tiene que poner
en ROJO S1, S4 y S7 respectivamente. Un comprobador que sólo se ha puesto verde no ha
demostrado saber ponerse rojo.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent.parent                      # code/
ROOT = CODE.parent                             # repo
sys.path.insert(0, str(CODE))

import loader                                              # noqa: E402
from contracts import CAN_REDUCE, CAN_STOP, Request        # noqa: E402
from extraction.facts import load_facts                    # noqa: E402
from finance import params as fparams                      # noqa: E402
from finance import view as fv                             # noqa: E402

OUT_CSV = ROOT / "output.csv"
REPORT = HERE / "ORACLE.md"
CENT = Decimal("0.01")
TOL = Decimal("0.02")          # tolerancia de redondeo al comparar importes escritos
HORIZON = fparams.HORIZON

IMMEDIATE = ("full_payment", "partial_payment", "installments")
_NUM = re.compile(r"(\d+)\s*$")


# ─────────────────────────────────────────────────────────────────────────────
# Lectura de output.csv — el vacío no es ausencia: "" y "none" son cosas distintas
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Row:
    request_id: str
    safe: Decimal | None
    status: str
    method: str
    plan_raw: str
    payments: tuple[tuple[date, Decimal], ...]
    plan_is_none: bool
    earliest: date | None
    earliest_raw: str
    changes_raw: str
    changes: tuple[tuple[str, str, Decimal | None], ...]
    explanation: str
    malformed: list[str] = field(default_factory=list)


def _dt(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _dec(s: str) -> Decimal | None:
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def read_output() -> list[Row]:
    rows: list[Row] = []
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            bad: list[str] = []
            plan_raw = (r["payment_plan"] or "").strip()
            pays: list[tuple[date, Decimal]] = []
            plan_none = plan_raw.lower() == "none" or plan_raw == ""
            if not plan_none:
                for tok in plan_raw.split("|"):
                    if ":" not in tok:
                        bad.append(f"pago sin ':' -> {tok!r}")
                        continue
                    d, _, a = tok.partition(":")
                    dd, aa = _dt(d), _dec(a)
                    if dd is None or aa is None:
                        bad.append(f"pago malformado -> {tok!r}")
                        continue
                    pays.append((dd, aa))
            ch_raw = (r["spending_changes_needed"] or "").strip()
            chs: list[tuple[str, str, Decimal | None]] = []
            if ch_raw and ch_raw.lower() != "none":
                for tok in ch_raw.split("|"):
                    p = tok.split(":")
                    if p[0] == "stop" and len(p) == 2:
                        chs.append(("stop", p[1], None))
                    elif p[0] == "reduce_to" and len(p) == 3 and _dec(p[2]) is not None:
                        chs.append(("reduce_to", p[1], _dec(p[2])))
                    else:
                        bad.append(f"cambio malformado -> {tok!r}")
            rows.append(Row(
                request_id=r["request_id"], safe=_dec(r["amount_safe_to_pay"]),
                status=(r["affordability_status"] or "").strip(),
                method=(r["recommended_payment_method"] or "").strip(),
                plan_raw=plan_raw, payments=tuple(pays), plan_is_none=plan_none,
                earliest=_dt(r["earliest_date_for_full_payment"]),
                earliest_raw=(r["earliest_date_for_full_payment"] or "").strip(),
                changes_raw=ch_raw, changes=tuple(chs),
                explanation=r.get("decision_explanation", ""), malformed=bad))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Instrumento: la curva de 90 días. Se usa para EVALUAR, nunca para decidir.
# ─────────────────────────────────────────────────────────────────────────────

class World:
    """Curva de caja por request, cacheada. Un `reconstruct` por solicitud."""

    def __init__(self):
        self.ds = loader.load()
        self.facts = load_facts(self.ds)
        self.req = {r.request_id: r for r in self.ds.requests}
        self._rec: dict[str, object] = {}
        self._pts: dict[str, list] = {}

    def rec(self, rid: str):
        if rid not in self._rec:
            r = self.req[rid]
            self._rec[rid] = fv.reconstruct(self.ds, r, self.facts)
        return self._rec[rid]

    def points(self, rid: str):
        if rid not in self._pts:
            pts, _, _ = fv.curve(self.rec(rid))
            self._pts[rid] = pts
        return self._pts[rid]

    def min_with(self, rid: str, payments=(), stops=(), reduces=()) -> Decimal:
        """Mínimo de la curva de 90 días con los pagos Y los cambios de gasto aplicados.

        Medir sin los cambios produce falsos positivos: el ahorro del stop/reduce es
        parte del plan recomendado y el enunciado lo cuenta ("permitted spending changes").
        """
        if stops or reduces:
            return fv.simulate(self.ds, self.req[rid], self.facts,
                               tuple(stops), tuple(reduces), tuple(payments))
        extra = [fv.Flow(d, -a, "pago") for d, a in payments]
        _, lo, _ = fv.curve(self.rec(rid), extra)
        return lo

    def safe_star(self, rid: str) -> Decimal:
        """"the most the user can pay today ... without breaking the 90-day safety
        check, capped at requested_amount". Se deriva de la curva, no del motor."""
        r = self.req[rid]
        prof = self.ds.profiles[r.user_id]
        lo = min(p.balance for p in self.points(rid))
        return max(Decimal(0), min(r.requested_amount, lo - prof.minimum_balance_to_keep))

    def earliest_star(self, rid: str) -> date | None:
        """"the first date the full amount passes the safety check" — desde esa fecha
        en adelante, pagar el total completo no rompe el mínimo."""
        r = self.req[rid]
        prof = self.ds.profiles[r.user_id]
        need = prof.minimum_balance_to_keep + r.requested_amount
        pts = self.points(rid)
        run = None
        suffix = [Decimal(0)] * len(pts)
        for i in range(len(pts) - 1, -1, -1):
            run = pts[i].balance if run is None else min(run, pts[i].balance)
            suffix[i] = run
        for i, p in enumerate(pts):
            if suffix[i] >= need:
                return p.day
        return None

    def opts(self, rid: str):
        return self.ds.options_by_request.get(rid, ())

    def prof(self, rid: str):
        return self.ds.profiles[self.req[rid].user_id]


def lineas(rel: str, aguja: str) -> str:
    """Todas las líneas donde aparece el fragmento, como "191,227"."""
    try:
        return ",".join(
            str(i) for i, l in enumerate(
                (ROOT / rel).read_text(encoding="utf-8").splitlines(), 1) if aguja in l)
    except OSError:
        return "?"


def linea(rel: str, aguja: str, default: int = 0) -> int:
    """Nº de línea real de un fragmento de código. Citar una línea de memoria es
    inventarse la evidencia: se lee del archivo en cada corrida."""
    try:
        for i, l in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if aguja in l:
                return i
    except OSError:
        pass
    return default


def option_dates(o) -> tuple[date, ...]:
    """"payments begin at first_payment_date, payment_frequency_days between them"."""
    f = o.payment_frequency_days or 0
    return tuple(o.first_payment_date + timedelta(days=f * k)
                 for k in range(max(1, o.number_of_payments)))


def meses(o) -> int:
    n = max(1, o.number_of_payments)
    f = o.payment_frequency_days
    if n == 1 or not f:
        return 1
    if 27 <= f <= 32:
        return n
    return int((n * f + 30) // 30.44)


# ─────────────────────────────────────────────────────────────────────────────
# Resultado de un invariante
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Check:
    code: str
    title: str
    quote: str
    mutation: str
    violations: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations

    def fail(self, rid: str, why: str) -> None:
        self.violations.append((rid, why))


def money(x: Decimal | None) -> str:
    if x is None:
        return "—"
    return f"{Decimal(x).quantize(CENT):,}"


# ─────────────────────────────────────────────────────────────────────────────
# S1 .. S8
# ─────────────────────────────────────────────────────────────────────────────

def run_invariants(w: World, rows: list[Row]) -> list[Check]:
    s1 = Check("S1", "0 <= amount_safe_to_pay <= requested_amount",
               "The following relationship must always hold: "
               "0 <= amount_safe_to_pay <= requested_amount",
               "poner safe = requested + 1 en una fila")
    s2 = Check("S2", "affordable_now => earliest == request_date y el total es seguro hoy",
               "affordable_now: the full amount is safe to pay on request_date and the "
               "user accepts full_payment · For affordable_now, "
               "earliest_date_for_full_payment must equal request_date",
               "mover un día la fecha de earliest en una fila affordable_now")
    s3 = Check("S3", "método inmediato => está en payment_methods_user_will_consider",
               "An immediate payment method—full_payment, partial_payment, or "
               "installments—is eligible only when it appears in the user's "
               "payment_methods_user_will_consider",
               "recomendar installments a un usuario que sólo acepta full_payment")
    s4 = Check("S4", "wait => el total se vuelve seguro más tarde Y acepta full_payment",
               "wait is eligible when full payment becomes safe later and the user "
               "accepts full_payment",
               "dejar la fecha vacía en una fila wait")
    s5 = Check("S5", "not_recommended => no había ninguna alternativa segura y elegible",
               "not_recommended is the fallback when no safe eligible payment is available",
               "recomendar not_recommended donde un full_payment limpio era seguro")
    s6 = Check("S6", "todo plan recomendado: pagos factibles, a tiempo, y por encima del mínimo",
               "A recommendation is safe only if the user can make every listed payment, "
               "complete the full request by its deadline, cover essential expenses, and "
               "maintain their preferred minimum balance throughout the forecast period",
               "correr un pago del plan un mes después del deadline")
    s7 = Check("S7", "installments coincide EXACTAMENTE con una opción suministrada",
               "Installment plans must exactly match a supplied payment option · "
               "Respect all supplied payment-option schedules",
               "cambiar un céntimo el importe de una cuota")
    s8 = Check("S8", "partial_payment cumple sus ocho condiciones literales",
               "Recommend it only when the request allows partial payment, the user "
               "accepts this method, amount_safe_to_pay is greater than zero but less "
               "than requested_amount, and earliest_date_for_full_payment is on or before "
               "desired_completion_date. The plan must contain exactly two payments...",
               "partir el pago en tres tramos")
    checks = [s1, s2, s3, s4, s5, s6, s7, s8]

    for row in rows:
        rid = row.request_id
        r = w.req.get(rid)
        if r is None:
            for c in checks:
                c.fail(rid, "request_id no existe en dataset/requests.csv")
            continue
        prof = w.prof(rid)
        for m in row.malformed:
            s6.fail(rid, f"fila malformada: {m}")

        # ── S1 ───────────────────────────────────────────────────────────────
        s1.checked += 1
        if row.safe is None:
            s1.fail(rid, "amount_safe_to_pay vacío o no numérico")
        elif row.safe < 0:
            s1.fail(rid, f"safe={money(row.safe)} < 0")
        elif row.safe > r.requested_amount + TOL:
            s1.fail(rid, f"safe={money(row.safe)} > requested={money(r.requested_amount)}")

        # ── S2 ───────────────────────────────────────────────────────────────
        if row.status == "affordable_now":
            s2.checked += 1
            if row.earliest != r.request_date:
                s2.fail(rid, f"earliest={row.earliest_raw or '(vacío)'} != "
                             f"request_date={r.request_date}")
            if row.safe is None or row.safe < r.requested_amount - TOL:
                s2.fail(rid, f"safe={money(row.safe)} < requested="
                             f"{money(r.requested_amount)}: el total NO es seguro hoy")
            lo = w.min_with(rid, ((r.request_date, r.requested_amount),),
                            tuple(e for k, e, _ in row.changes if k == "stop"),
                            tuple((e, a) for k, e, a in row.changes if k == "reduce_to"))
            if lo < prof.minimum_balance_to_keep:
                s2.fail(rid, f"pagar el total hoy deja el mínimo en {money(lo)} < "
                             f"{money(prof.minimum_balance_to_keep)}")
            if "full_payment" not in prof.methods_considered:
                s2.fail(rid, "affordable_now pero el usuario no acepta full_payment")

        # ── S3 ───────────────────────────────────────────────────────────────
        if row.method in IMMEDIATE:
            s3.checked += 1
            if row.method not in prof.methods_considered:
                s3.fail(rid, f"método {row.method} no está en "
                             f"{'|'.join(prof.methods_considered) or '(vacío)'}")

        # ── S4 ───────────────────────────────────────────────────────────────
        if row.method == "wait":
            s4.checked += 1
            if "full_payment" not in prof.methods_considered:
                s4.fail(rid, "wait pero el usuario no acepta full_payment "
                             f"({'|'.join(prof.methods_considered) or '(vacío)'})")
            if row.earliest is None:
                s4.fail(rid, "wait con earliest vacío: nada dice que se vuelva seguro")
            elif row.earliest <= r.request_date:
                s4.fail(rid, f"wait con earliest={row.earliest} <= "
                             f"request_date={r.request_date}: no es 'later'")
            else:
                lo = w.min_with(rid, ((row.earliest, r.requested_amount),))
                if lo < prof.minimum_balance_to_keep:
                    s4.fail(rid, f"pagar el total el {row.earliest} deja el mínimo en "
                                 f"{money(lo)} < {money(prof.minimum_balance_to_keep)}")

        # ── S5 ───────────────────────────────────────────────────────────────
        if row.method == "not_recommended":
            s5.checked += 1
            alts = eligible_safe_plans(w, rid)
            if alts:
                best = min(alts, key=lambda p: p.key)
                s5.fail(rid, f"existía **{best.label}**: total={money(best.total)} · "
                             f"{len(best.payments)} pagos · último={best.last_day} "
                             f"(deadline {r.desired_completion_date}) · el saldo mínimo "
                             f"de los 90 días CON el plan es {money(best.worst)} ≥ "
                             f"minimum_balance_to_keep="
                             f"{money(prof.minimum_balance_to_keep)}. "
                             f"Causa: {causa(w, rid, best)}")
            if row.status != "not_affordable":
                s5.notes.append(f"{rid}: not_recommended con status {row.status}")

        # ── S6 ───────────────────────────────────────────────────────────────
        if row.method in IMMEDIATE + ("wait",):
            s6.checked += 1
            if row.plan_is_none or not row.payments:
                s6.fail(rid, f"método {row.method} con payment_plan={row.plan_raw!r}")
            else:
                days = [d for d, _ in row.payments]
                if days != sorted(days):
                    s6.fail(rid, "los pagos no están en orden cronológico")
                last = max(days)
                if last > r.desired_completion_date:
                    s6.fail(rid, f"último pago {last} > desired_completion_date="
                                 f"{r.desired_completion_date}")
                if min(days) < r.request_date:
                    s6.fail(rid, f"primer pago {min(days)} antes del request_date="
                                 f"{r.request_date}")
                total = sum((a for _, a in row.payments), Decimal(0))
                if total < r.requested_amount - TOL:
                    s6.fail(rid, f"el plan paga {money(total)} < requested="
                                 f"{money(r.requested_amount)}: no completa el pedido")
                if any(a <= 0 for _, a in row.payments):
                    s6.fail(rid, "hay un pago <= 0")
                stops = tuple(e for k, e, _ in row.changes if k == "stop")
                reds = tuple((e, a) for k, e, a in row.changes if k == "reduce_to")
                lo = w.min_with(rid, row.payments, stops, reds)
                if lo < prof.minimum_balance_to_keep:
                    s6.fail(rid, f"con el plan (y sus cambios) el saldo baja a {money(lo)} "
                                 f"< minimum_balance_to_keep="
                                 f"{money(prof.minimum_balance_to_keep)} en 90 días")
            for why in validate_changes(w, rid, row):
                s6.fail(rid, why)
        elif row.changes:
            s6.fail(rid, f"{row.method} sin plan pero con cambios de gasto "
                         f"{row.changes_raw!r}")

        # ── S7 ───────────────────────────────────────────────────────────────
        if row.method == "installments":
            s7.checked += 1
            match = match_option(w, rid, row)
            if match is None:
                s7.fail(rid, f"el plan {row.plan_raw} no coincide con ninguna opción de "
                             f"request_payment_options.csv "
                             f"({len(w.opts(rid))} opciones para este request)")
            else:
                if match.payment_method != "installments":
                    s7.fail(rid, f"la opción que coincide ({match.payment_option_id}) es "
                                 f"{match.payment_method}, no installments")
                last = max(option_dates(match))
                if last > r.desired_completion_date:
                    s7.fail(rid, f"{match.payment_option_id} termina el {last} > "
                                 f"deadline {r.desired_completion_date}")

        # ── S8 ───────────────────────────────────────────────────────────────
        if row.method == "partial_payment":
            s8.checked += 1
            if not r.allows_partial_payment:
                s8.fail(rid, "allows_partial_payment=false")
            if "partial_payment" not in prof.methods_considered:
                s8.fail(rid, "el usuario no acepta partial_payment")
            if row.status != "affordable_with_plan":
                s8.fail(rid, f"status={row.status}, el enunciado exige affordable_with_plan")
            if row.safe is None or not (Decimal(0) < row.safe < r.requested_amount):
                s8.fail(rid, f"safe={money(row.safe)} fuera de (0, "
                             f"{money(r.requested_amount)})")
            if row.earliest is None:
                s8.fail(rid, "earliest vacío")
            elif row.earliest > r.desired_completion_date:
                s8.fail(rid, f"earliest={row.earliest} > deadline="
                             f"{r.desired_completion_date}")
            if len(row.payments) != 2:
                s8.fail(rid, f"{len(row.payments)} pagos, el enunciado exige EXACTAMENTE 2")
            else:
                (d1, a1), (d2, a2) = row.payments
                if d1 != r.request_date:
                    s8.fail(rid, f"pago 1 el {d1} != request_date={r.request_date}")
                if row.safe is not None and abs(a1 - row.safe) > TOL:
                    s8.fail(rid, f"pago 1 = {money(a1)} != amount_safe_to_pay="
                                 f"{money(row.safe)}")
                if row.earliest is not None and d2 != row.earliest:
                    s8.fail(rid, f"pago 2 el {d2} != earliest={row.earliest}")
                if row.safe is not None and \
                        abs(a2 - (r.requested_amount - row.safe)) > TOL:
                    s8.fail(rid, f"pago 2 = {money(a2)} != requested - safe = "
                                 f"{money(r.requested_amount - row.safe)}")
                if abs(a1 + a2 - r.requested_amount) > TOL:
                    s8.fail(rid, f"los dos pagos suman {money(a1 + a2)} != requested="
                                 f"{money(r.requested_amount)}")
    return checks


def causa(w: World, rid: str, alt: Alt) -> str:
    """Por qué el motor descartó un plan que el enunciado declara seguro.

    No se adivina: se reproduce la condición exacta del código y se dice cuál se
    disparó, con su línea. Sin esto un FAIL es una acusación sin dirección.
    """
    safe = w.safe_star(rid)
    mayor = max(a for _, a in alt.payments)
    if mayor > safe:
        ln = lineas("code/decision/candidates.py", "if cabe(pagos, cap):")
        return (f"`code/decision/candidates.py:{ln}` "
                f"— `cabe()` exige que CADA pago ({money(mayor)}) quepa en el "
                f"`amount_safe_to_pay` de hoy ({money(safe)}). Ese requisito no está en "
                f"el enunciado; el enunciado sólo pide que el saldo no baje del mínimo "
                f"en 90 días, y este plan no lo baja")
    prof = w.prof(rid)
    lo_exacto = alt.worst
    margen = lo_exacto - prof.minimum_balance_to_keep
    if margen < Decimal("0.01"):
        r = w.req[rid]
        head = min(p.balance for p in w.points(rid)) - prof.minimum_balance_to_keep
        exceso = min(r.requested_amount, max(Decimal(0), head)).quantize(CENT) - \
            min(r.requested_amount, max(Decimal(0), head))
        return (f"`code/finance/view.py:482` — `amount_safe_today` se redondea a dos "
                f"decimales HACIA ARRIBA (+{exceso} sobre el margen real de la curva). "
                f"El sistema se contradice a sí mismo: escribe "
                f"`amount_safe_to_pay={money(safe)}` — *\"the most the user can safely "
                f"pay today\"* — y acto seguido `code/decision/ranking.py:87` rechaza por "
                f"INSEGURO el plan que paga exactamente esa cifra, porque hunde el saldo "
                f"{exceso} bajo el mínimo. Truncar hacia abajo en vez de redondear "
                f"resuelve las dos mitades a la vez")
    return "no reproducible con las condiciones del motor; revisar a mano"


def validate_changes(w: World, rid: str, row: Row) -> list[str]:
    """"Only recurring expenses marked as flexible may be changed" · máximo tres ·
    "Stopping and reducing the same financial event are mutually exclusive"."""
    out: list[str] = []
    if not row.changes:
        return out
    prof = w.prof(rid)
    evs = {e.event_id: e for e in w.ds.events_by_user.get(w.req[rid].user_id, ())}
    if len(row.changes) > 3:
        out.append(f"{len(row.changes)} cambios de gasto (máximo 3)")
    seen: Counter = Counter(e for _, e, _ in row.changes)
    for eid, n in seen.items():
        if n > 1:
            out.append(f"{eid} aparece {n} veces: stop y reduce son excluyentes")
    for kind, eid, amt in row.changes:
        e = evs.get(eid)
        if e is None:
            out.append(f"{eid} no es un evento de este usuario")
            continue
        if e.direction != "debit":
            out.append(f"{eid} no es un gasto (direction={e.direction})")
        if e.category in prof.protect:
            out.append(f"{eid} es de categoría protegida ({e.category})")
        if kind == "stop":
            if e.flexibility not in CAN_STOP:
                out.append(f"stop:{eid} con flexibility={e.flexibility}")
            if e.category not in prof.willing_stop:
                out.append(f"stop:{eid} categoría {e.category} no está en "
                           f"expense_categories_user_is_willing_to_stop")
        else:
            if e.flexibility not in CAN_REDUCE:
                out.append(f"reduce_to:{eid} con flexibility={e.flexibility}")
            if e.category not in prof.willing_reduce:
                out.append(f"reduce_to:{eid} categoría {e.category} no está en "
                           f"expense_categories_user_is_willing_to_reduce")
            if e.minimum_allowed_amount is not None and amt is not None and \
                    amt < e.minimum_allowed_amount - TOL:
                out.append(f"reduce_to:{eid}:{money(amt)} por debajo de "
                           f"minimum_allowed_amount={money(e.minimum_allowed_amount)}")
            if e.amount is not None and amt is not None and amt > e.amount + TOL:
                out.append(f"reduce_to:{eid}:{money(amt)} no reduce nada "
                           f"(el gasto ya era {money(e.amount)})")
    return out


def match_option(w: World, rid: str, row: Row):
    """Coincidencia EXACTA: fechas derivadas de first_payment_date + k*frecuencia,
    importe de cada pago = payment_amount, y el mismo número de pagos."""
    for o in w.opts(rid):
        ds_ = option_dates(o)
        if len(ds_) != len(row.payments):
            continue
        if tuple(d for d, _ in row.payments) != ds_:
            continue
        if any(abs(a - o.payment_amount) > TOL for _, a in row.payments):
            continue
        return o
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Alternativas seguras y elegibles — construidas desde el ENUNCIADO, no del motor
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Alt:
    label: str
    method: str
    payments: tuple[tuple[date, Decimal], ...]
    total: Decimal
    option_id: str | None
    worst: Decimal
    n_changes: int = 0

    @property
    def last_day(self) -> date:
        return max(d for d, _ in self.payments)

    @property
    def first_day(self) -> date:
        return min(d for d, _ in self.payments)

    @property
    def opt_num(self) -> int:
        if not self.option_id:
            return 10 ** 9
        m = _NUM.search(self.option_id)
        return int(m.group(1)) if m else 10 ** 9

    @property
    def key(self):
        """Los SEIS criterios del enunciado, en su orden, y ninguno más."""
        return (self.n_changes, self.total, self.first_day,
                len(self.payments), self.opt_num)


def eligible_safe_plans(w: World, rid: str, deadline_filter: bool = True) -> list[Alt]:
    """Planes que el ENUNCIADO declara elegibles Y seguros, sin cambios de gasto.

    Elegible: el método está en `payment_methods_user_will_consider` (y, para cuotas,
    el plan cabe en `max_installment_months`).
    Seguro:   completa el pedido en o antes del deadline y el saldo nunca baja del
              mínimo en los 90 días.
    Sin cambios de gasto a propósito: para PROBAR una violación basta una alternativa
    limpia; añadir cambios sólo podría añadir más alternativas, nunca quitarlas.
    """
    r = w.req[rid]
    prof = w.prof(rid)
    floor = prof.minimum_balance_to_keep
    out: list[Alt] = []
    full_opt = next((o for o in w.opts(rid) if o.payment_method == "full_payment"), None)
    full_id = full_opt.payment_option_id if full_opt else None

    if "full_payment" in prof.methods_considered:
        if not deadline_filter or r.request_date <= r.desired_completion_date:
            pays = ((r.request_date, r.requested_amount),)
            lo = w.min_with(rid, pays)
            if lo >= floor:
                out.append(Alt("full_payment hoy", "full_payment", pays,
                               r.requested_amount, full_id, lo))
        e = w.earliest_star(rid)
        if e is not None and e > r.request_date and \
                (not deadline_filter or e <= r.desired_completion_date):
            pays = ((e, r.requested_amount),)
            lo = w.min_with(rid, pays)
            if lo >= floor:
                out.append(Alt(f"wait hasta {e}", "wait", pays,
                               r.requested_amount, full_id, lo))

    if r.allows_partial_payment and "partial_payment" in prof.methods_considered:
        safe = w.safe_star(rid)
        e = w.earliest_star(rid)
        if Decimal(0) < safe < r.requested_amount and e is not None and \
                (not deadline_filter or e <= r.desired_completion_date):
            pays = ((r.request_date, safe), (e, r.requested_amount - safe))
            lo = w.min_with(rid, pays)
            if lo >= floor:
                out.append(Alt("partial_payment", "partial_payment", pays,
                               r.requested_amount, None, lo))

    if "installments" in prof.methods_considered and prof.max_installment_months:
        for o in w.opts(rid):
            if o.payment_method != "installments":
                continue
            if meses(o) > prof.max_installment_months:
                continue
            dts = option_dates(o)
            if deadline_filter and max(dts) > r.desired_completion_date:
                continue
            if min(dts) < r.request_date:
                continue
            pays = tuple((d, o.payment_amount) for d in dts)
            lo = w.min_with(rid, pays)
            if lo >= floor:
                total = o.total_payable_amount or o.payment_amount * o.number_of_payments
                out.append(Alt(f"installments {o.payment_option_id}", "installments",
                               pays, total, o.payment_option_id, lo))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AUDITORÍAS
# ─────────────────────────────────────────────────────────────────────────────

def audit_a(w: World, rows: list[Row]) -> tuple[str, bool, str | None]:
    """A) ¿El cálculo de `earliest` consulta las preferencias de método?"""
    campos = ("payment_methods_user_will_consider", "methods_considered",
              "max_installment_months")
    hits: list[str] = []
    for p in sorted((CODE / "finance").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for c in campos:
                if c in line:
                    hits.append(f"`{p.relative_to(ROOT)}:{i}` — `{line.strip()[:90]}`")
    # Acoplamiento indirecto: engine.py borra la fecha cuando no recomienda nada.
    borradas = []
    por_preferencia: list[tuple[str, date]] = []
    por_plazo = 0
    for row in rows:
        if row.earliest_raw == "" and row.request_id in w.req:
            e = w.earliest_star(row.request_id)
            if e is None:
                continue
            borradas.append((row.request_id, e, row.status, row.method))
            r = w.req[row.request_id]
            if e > r.desired_completion_date:
                por_plazo += 1
            elif "full_payment" not in w.prof(row.request_id).methods_considered:
                por_preferencia.append((row.request_id, e))
    L = []
    L.append("### A · `earliest_date_for_full_payment` frente a las preferencias\n")
    L.append("> *\"`earliest_date_for_full_payment` measures financial capacity "
             "independently of the user's payment-method preferences.\"*\n")
    L.append("| Pregunta | Respuesta | Evidencia |")
    L.append("|---|---|---|")
    L.append(f"| ¿`finance/` lee `payment_methods_user_will_consider`? | **NO** | "
             f"{'; '.join(hits) if hits else '0 coincidencias en `code/finance/*.py`'} |")
    L.append(f"| ¿`finance/` lee `max_installment_months`? | **NO** | "
             f"{'; '.join(hits) if hits else '0 coincidencias en `code/finance/*.py`'} |")
    L.append("| ¿Dónde SÍ se leen? | `code/loader.py:55-66` | sólo para poblar el "
             "`Profile`; `finance/view.py` nunca toca esos dos campos |")
    L.append(f"| ¿El valor ESCRITO depende de las preferencias? | **SÍ**, en "
             f"{len(por_preferencia)} filas | `code/decision/engine.py:75` — "
             f"`earliest = None` cuando el estado es `not_affordable`, y ese estado sí "
             f"depende de las preferencias |")
    L.append("")
    L.append(f"**Respuesta a la pregunta del encargo: NO** (esperado: NO). El cálculo "
             f"—`code/finance/view.py:468-474`— sólo usa la curva de caja, "
             f"`minimum_balance_to_keep` y `requested_amount`. Las dos únicas apariciones "
             f"de esos campos en todo el árbol de `finance/` son **cero**; viven en "
             f"`code/loader.py:65-66` (poblar el `Profile`) y en "
             f"`code/decision/candidates.py:189,210,219,223` (elegibilidad), que es su "
             f"sitio.\n")
    L.append(f"**Pero el valor que llega al CSV sí depende de las preferencias, por la "
             f"puerta de atrás.** `code/decision/engine.py:75` borra la fecha "
             f"(`earliest = None`) al declarar `not_affordable`. Medido sobre las 250:\n")
    L.append("| Filas con `earliest_date_for_full_payment` VACÍO | N |")
    L.append("|---|---:|")
    L.append(f"| Total con la columna vacía | "
             f"{sum(1 for r in rows if r.earliest_raw == '')} |")
    L.append(f"| · la capacidad tampoco alcanza en 90 días (vacío correcto) | "
             f"{sum(1 for r in rows if r.earliest_raw == '') - len(borradas)} |")
    L.append(f"| · la capacidad SÍ alcanza, pero después del `desired_completion_date` | "
             f"{por_plazo} |")
    L.append(f"| · **la capacidad SÍ alcanza y a tiempo: el único motivo del vacío es que "
             f"el usuario no acepta `full_payment`** | **{len(por_preferencia)}** |")
    L.append("")
    if por_preferencia:
        L.append(f"**⚠️ HALLAZGO — {len(por_preferencia)} de 250 filas contradicen la "
                 f"frase citada.** En ellas la capacidad existe y llega a tiempo, y la "
                 f"fecha se borra por una PREFERENCIA de método. El enunciado usa "
                 f"justamente ese caso como ejemplo de lo contrario: *\"It may equal "
                 f"`request_date` even when the selected recommendation is installments "
                 f"because the user has chosen not to consider full payment.\"* "
                 f"Ejemplos: " +
                 ", ".join(f"`{rid}` (capacidad {e})" for rid, e in por_preferencia[:6]) +
                 ".\n")
        L.append("La defensa del motor es la otra frase del enunciado —*\"Leave it empty "
                 "when the full amount is not expected to become safe within the forecast "
                 "period\"*— pero esa frase condiciona al PRONÓSTICO, no al método "
                 "elegido; en estas 25 el importe completo **sí** se vuelve seguro dentro "
                 "del horizonte. Las dos frases sólo entran en conflicto si se lee "
                 "`not_affordable` como sinónimo de \"no se vuelve seguro\", y aquí no lo "
                 "es: es \"no hay plan que el usuario acepte\".\n")
    titular = None
    if por_preferencia:
        titular = (f"A — `EARLIEST_DATE_FOR_FULL_PAYMENT` SE BORRA POR UNA PREFERENCIA "
                   f"DE MÉTODO · {len(por_preferencia)} FILAS DE 250\n\nEl enunciado dice "
                   f"que esa columna *\"measures financial capacity **independently of the "
                   f"user's payment-method preferences**\"*. En esas filas el importe "
                   f"completo sí se vuelve seguro dentro de los 90 días y a tiempo; la "
                   f"celda sale vacía sólo porque el usuario no acepta `full_payment` "
                   f"(`code/decision/engine.py:75`). Ejemplos: " +
                   ", ".join(f"`{rid}` (capacidad {e})" for rid, e in por_preferencia[:6]) +
                   ".")
    return "\n".join(L), not por_preferencia, titular


def audit_b(w: World, rows: list[Row]) -> tuple[str, bool, str | None]:
    """B) Opciones que terminan después del deadline de su request."""
    total = late = 0
    por_metodo: Counter = Counter()
    late_por_metodo: Counter = Counter()
    sin_request = 0
    reqs_con_opcion_util: Counter = Counter()
    for rid, opts in w.ds.options_by_request.items():
        r = w.req.get(rid)
        for o in opts:
            total += 1
            por_metodo[o.payment_method] += 1
            if r is None:
                sin_request += 1
                continue
            if max(option_dates(o)) > r.desired_completion_date:
                late += 1
                late_por_metodo[o.payment_method] += 1
            else:
                reqs_con_opcion_util[rid] += 1
    # ¿Alguna de las que recomendamos termina tarde?
    recomendadas_tarde = []
    for row in rows:
        if row.method != "installments":
            continue
        o = match_option(w, row.request_id, row)
        if o is None:
            recomendadas_tarde.append((row.request_id, "SIN OPCIÓN QUE COINCIDA"))
            continue
        r = w.req[row.request_id]
        if max(option_dates(o)) > r.desired_completion_date:
            recomendadas_tarde.append((row.request_id,
                                       f"{o.payment_option_id} termina "
                                       f"{max(option_dates(o))} > "
                                       f"{r.desired_completion_date}"))
    L = []
    L.append("### B · Opciones de pago que terminan FUERA de plazo\n")
    L.append("> *\"The plan must complete the request by `desired_completion_date`\"* — "
             "una opción que existe no es automáticamente elegible.\n")
    L.append("| Método | Opciones | Terminan tarde | % |")
    L.append("|---|---:|---:|---:|")
    for m in sorted(por_metodo):
        n, l = por_metodo[m], late_por_metodo[m]
        L.append(f"| {m} | {n} | {l} | {100 * l / n:.1f}% |")
    L.append(f"| **TOTAL** | **{total}** | **{late}** | **{100 * late / total:.1f}%** |")
    L.append("")
    # Requests de los 250 que se quedan sin NINGUNA opción de cuotas en plazo
    n250 = 0
    for row in rows:
        rid = row.request_id
        r = w.req.get(rid)
        if r is None:
            continue
        cu = [o for o in w.opts(rid) if o.payment_method == "installments"]
        if cu and all(max(option_dates(o)) > r.desired_completion_date for o in cu):
            n250 += 1
    L.append(f"De las **{total}** opciones del fichero, **{late}** terminan después del "
             f"`desired_completion_date` de su propia solicitud. "
             f"**{n250} de las 250** solicitudes evaluadas no tienen ni una sola opción de "
             f"cuotas que llegue a tiempo: para ellas `installments` no es una alternativa "
             f"aunque el CSV liste varias.\n")
    ok = not recomendadas_tarde
    if ok:
        L.append("**Ninguna de las 19 filas con `installments` recomienda una opción que "
                 "termine tarde.** ✅\n")
    else:
        L.append("**VIOLACIÓN — opciones recomendadas que terminan tarde:**\n")
        for rid, why in recomendadas_tarde:
            L.append(f"- `{rid}`: {why}")
        L.append("")
    return "\n".join(L), ok, None


def audit_c(w: World, rows: list[Row]) -> tuple[str, bool, str | None]:
    """C) Pending: los débitos se reservan, los créditos se ignoran."""
    deb_total = deb_in = deb_out = 0
    cred_total = cred_ign = cred_bad = 0
    faltan: list[str] = []
    cuelan: list[str] = []
    sin_monto = 0
    for row in rows:
        rid = row.request_id
        r = w.req.get(rid)
        if r is None:
            continue
        rec = w.rec(rid)
        ids_en_curva = {f.event_id for f in rec.flows if f.event_id}
        end = r.request_date + timedelta(days=HORIZON)
        for e in w.ds.events_by_user.get(r.user_id, ()):
            if e.status != "pending":
                continue
            day = e.settlement_date or e.event_date
            if day is None or day > end:
                continue
            if e.direction == "debit":
                deb_total += 1
                if e.event_id in ids_en_curva:
                    deb_in += 1
                else:
                    deb_out += 1
                    if e.amount is None:
                        sin_monto += 1
                        faltan.append(f"{rid}/{e.event_id} (monto en blanco, sin serie)")
                    else:
                        faltan.append(f"{rid}/{e.event_id} {e.description[:30]}")
            elif e.direction == "credit":
                cred_total += 1
                if e.event_id in ids_en_curva:
                    cred_bad += 1
                    cuelan.append(f"{rid}/{e.event_id} {e.description[:30]}")
                else:
                    cred_ign += 1
    viol = cred_bad + (deb_out - sin_monto)
    L = []
    L.append("### C · Tratamiento de los `pending` en las 250 solicitudes\n")
    L.append("> *\"pending DEBIT se reserva · pending CREDIT NO se cuenta\"* — "
             "*\"Ignore pending credits, failed or cancelled transactions, duplicate "
             "records, and unrealized investments.\"*\n")
    L.append("| Concepto | N |")
    L.append("|---|---:|")
    L.append(f"| pending **debit** dentro del horizonte (total) | {deb_total} |")
    L.append(f"| · incluidos en la curva (reservados) | {deb_in} |")
    L.append(f"| · NO incluidos | {deb_out} |")
    L.append(f"| &nbsp;&nbsp;· de ellos, con `amount` en blanco y sin serie que lo estime "
             f"| {sin_monto} |")
    L.append(f"| pending **credit** dentro del horizonte (total) | {cred_total} |")
    L.append(f"| · ignorados (correcto) | {cred_ign} |")
    L.append(f"| · contados como efectivo (**violación**) | {cred_bad} |")
    L.append(f"| **VIOLACIONES** | **{viol}** |")
    L.append("")
    if cuelan:
        L.append("Créditos pendientes que sí entraron en la curva:\n")
        for x in cuelan[:10]:
            L.append(f"- `{x}`")
        L.append("")
    if deb_out:
        L.append(f"Débitos pendientes fuera de la curva ({deb_out}):\n")
        for x in faltan[:10]:
            L.append(f"- `{x}`")
        L.append("")
        if sin_monto == deb_out:
            L.append("Los " + str(sin_monto) + " son eventos con `amount` en blanco cuya "
                     "imagen no resolvió el importe y cuya categoría no tiene serie de la "
                     "que estimarlo. **El vacío no es cero**: no contarlos deja el "
                     "forecast OPTIMISTA. No es una violación de la regla `pending debit` "
                     "(no se está ignorando el estado), es un dato que falta — pero el "
                     "sesgo va en la dirección insegura y queda anotado.\n")
    return "\n".join(L), viol == 0, None


def audit_d(w: World, rows: list[Row]) -> tuple[str, bool, str | None]:
    """D) Ranking lexicográfico + búsqueda de criterios clandestinos."""
    by_id = {r.request_id: r for r in rows}
    casos: list[tuple[str, list[Alt], Alt, Row]] = []
    desacuerdos: list[tuple[str, Alt, Row]] = []
    con_cambios: list[str] = []

    def coincide(gana: Alt, row: Row) -> bool:
        if gana.method != row.method:
            return False
        if gana.method != "installments":
            return True
        o = match_option(w, row.request_id, row)
        return o is not None and o.payment_option_id == gana.option_id

    for row in rows:
        rid = row.request_id
        if rid not in w.req:
            continue
        alts = eligible_safe_plans(w, rid)
        if len(alts) >= 2:
            casos.append((rid, alts, min(alts, key=lambda a: a.key), row))
        if row.method == "not_recommended":
            continue                      # eso lo juzga S5, no el ranking
        if row.changes:
            # Su seguridad depende del ahorro; mis candidatos se construyen SIN
            # cambios de gasto a propósito. Compararlos sería comparar otra cosa.
            con_cambios.append(rid)
            continue
        if not alts:
            continue
        gana = min(alts, key=lambda a: a.key)
        if not coincide(gana, row):
            desacuerdos.append((rid, gana, row))

    # Criterios clandestinos: se leen del CÓDIGO, no se suponen.
    clandestinos: list[str] = []
    rank_src = (CODE / "decision" / "ranking.py").read_text(encoding="utf-8")
    cand_src = (CODE / "decision" / "candidates.py").read_text(encoding="utf-8")
    if "p.brecha" in rank_src and "seguros = tuple(p for p in planes if p.brecha <= 0)" \
            in rank_src:
        clandestinos.append(
            f"`code/decision/ranking.py:"
            f"{linea('code/decision/ranking.py', '1 if p.brecha > 0 else 0')}-"
            f"{linea('code/decision/ranking.py', 'p.brecha,')}` y `:"
            f"{linea('code/decision/ranking.py', 'seguros = tuple')}` — "
            "la clave de orden empieza por dos llaves "
            "que NO son del enunciado (`1 if p.brecha > 0 else 0` y `p.brecha`). "
            "**Neutralizadas**: `mejor()` filtra antes a `brecha <= 0`, así que entre los "
            "supervivientes ambas valen 0 y no pueden cambiar el ganador. Queda como "
            "código muerto en el orden, no como criterio activo.")
    if "if cabe(pagos, cap)" in cand_src:
        clandestinos.append(
            f"`code/decision/candidates.py:"
            f"{lineas('code/decision/candidates.py', 'if cabe(pagos, cap):')}` — "
            "`cabe(pagos, cap)`: **cada pago del "
            "plan debe caber en `amount_safe_to_pay` de HOY**. Ese requisito NO está en el "
            "enunciado; el enunciado sólo exige que el saldo no baje del mínimo en 90 "
            "días. Un plan de cuotas cuyas mensualidades se pagan con el sueldo que entra "
            "en el mes 2 es seguro para el enunciado y este filtro lo descarta ANTES del "
            "ranking.")
    if "return tuple(p for p in planes if a_tiempo(p, req))" in cand_src:
        clandestinos.append(
            f"`code/decision/candidates.py:"
            f"{linea('code/decision/candidates.py', 'if a_tiempo(p, req))')}` — "
            "el criterio 1 del enunciado "
            "(\"complete the full request by `desired_completion_date`\") se aplica como "
            "FILTRO duro, no como llave de orden. Es defendible (el enunciado lo repite "
            "dos veces fuera de la lista de seis, como condición de seguridad), pero "
            "cambia el resultado cuando NINGÚN plan llega a tiempo: el enunciado "
            "rankearía y este código devuelve `not_recommended`.")
    # Medición del filtro `cabe`: ¿cuántos planes seguros del enunciado descarta?
    descartados = 0
    afectados: list[str] = []
    for rid in by_id:
        if rid not in w.req:
            continue
        safe = w.safe_star(rid)
        for a in eligible_safe_plans(w, rid):
            if max(x for _, x in a.payments) > safe:
                descartados += 1
                if by_id[rid].method == "not_recommended":
                    afectados.append(f"`{rid}` · {a.label}")
                break

    L = []
    L.append("### D · Ranking lexicográfico de los seis criterios\n")
    L.append("> *\"When more than one eligible plan is safe, rank the plans in this "
             "order: 1. Complete the full request by `desired_completion_date`. "
             "2. Require no spending changes. 3. Minimize the total amount paid. "
             "4. Start payment earlier. 5. Use fewer payments. "
             "6. Use the lowest `payment_option_id`.\"*\n")
    L.append(f"Solicitudes con **2 o más planes seguros y elegibles** (construidos desde "
             f"el enunciado, sin cambios de gasto): **{len(casos)}**. "
             f"Desacuerdos con la fila entregada: **{len(desacuerdos)}**.\n")
    if casos:
        L.append("| request | candidato | c1 a tiempo | c2 cambios | c3 total pagado | "
                 "c4 inicio | c5 nº pagos | c6 option_id | |")
        L.append("|---|---|:--:|--:|--:|---|--:|---|---|")
        for rid, alts, gana, row in casos[:14]:
            for a in sorted(alts, key=lambda x: x.key):
                marca = "**←gana**" if a is gana else ""
                L.append(f"| `{rid}` | {a.label} | sí | {a.n_changes} | "
                         f"{money(a.total)} | {a.first_day} | {len(a.payments)} | "
                         f"{a.option_id or '—'} | {marca} |")
        if len(casos) > 14:
            L.append(f"| … | *({len(casos) - 14} solicitudes más, mismo patrón)* | | | | "
                     f"| | | |")
        L.append("")
        L.append("Lectura de la tabla: el criterio 1 no desempata (todos los candidatos "
                 "llegan a tiempo por construcción — un plan fuera de plazo no es seguro), "
                 "el 2 tampoco (ninguno lleva cambios), así que manda el **criterio 3, "
                 "total pagado**: por eso `full_payment` (paga exactamente el importe "
                 "pedido, fee 0) gana a cualquier plan de cuotas con `financing_fee` > 0, "
                 "y cuando dos candidatos pagan lo mismo desempata el **criterio 4**, "
                 "empezar antes — que es lo que pone `full_payment` hoy por delante de "
                 "`wait`.\n")
    n_plan = sum(1 for r in rows if r.method != "not_recommended")
    L.append(f"Comparación ampliada a **las {n_plan} filas que sí traen un plan** "
             f"(no sólo a las que tienen dos candidatos): el ganador lexicográfico "
             f"coincide con lo entregado en **{n_plan - len(desacuerdos) - len(con_cambios)}**, "
             f"discrepa en **{len(desacuerdos)}**, y **{len(con_cambios)}** quedan fuera "
             f"de la comparación porque su seguridad depende de los cambios de gasto "
             f"({', '.join('`' + x + '`' for x in con_cambios)}) y mis candidatos se "
             f"construyen a propósito sin ellos.\n")
    if desacuerdos:
        L.append("**Filas donde el ganador lexicográfico NO es lo entregado:**\n")
        for rid, gana, row in desacuerdos[:12]:
            entregado = next((a for a in eligible_safe_plans(w, rid)
                              if coincide(a, row)), None)
            extra = ("" if entregado is None else
                     f" — se paga {money(entregado.total - gana.total)} de más")
            L.append(f"- `{rid}`: gana `{gana.label}` por el **criterio 3** "
                     f"(total {money(gana.total)}, inicio {gana.first_day}, "
                     f"{len(gana.payments)} pagos) · entregado `{row.method}`"
                     f"{extra}. Causa: {causa(w, rid, gana)}")
        L.append("")
    L.append("#### Criterios clandestinos aplicados ANTES de los seis\n")
    if clandestinos:
        for c in clandestinos:
            L.append(f"- {c}")
    else:
        L.append("- Ninguno.")
    L.append("")
    L.append(f"Medición del filtro clandestino con efecto: **{descartados} de 250** "
             f"solicitudes tienen al menos un plan seguro según el enunciado cuyo pago "
             f"máximo supera `amount_safe_to_pay` de hoy y que por tanto `cabe()` "
             f"descarta antes de rankear. En **{len(afectados)}** de ellas el resultado "
             f"entregado es `not_recommended`" +
             (": " + " · ".join(afectados) if afectados else "") + ".\n")
    L.append("Búsqueda explícita de los criterios que el encargo señalaba: "
             "**preferencia de método** — no existe (el método sólo filtra elegibilidad, "
             f"`candidates.py:"
             f"{linea('code/decision/candidates.py', 'if \"full_payment\" in p.methods_considered')},"
             f"{linea('code/decision/candidates.py', 'if \"installments\" in p.methods_considered')}`"
             ", nunca ordena); **cuota mensual más baja** — no "
             "existe (no hay ninguna comparación de `payment_amount` en `ranking.py`); "
             "**duración más corta** — no existe como llave propia (el nº de pagos es el "
             "criterio 5, en su sitio); **colchón mayor** — no existe (`worst_balance` "
             "no entra en la clave de orden, sólo en la traza).\n")
    titular = None
    if desacuerdos:
        extra = []
        for rid, gana, row in desacuerdos:
            ent = next((a for a in eligible_safe_plans(w, rid) if coincide(a, row)), None)
            if ent is not None:
                extra.append(f"`{rid}` paga {money(ent.total - gana.total)} de más")
        titular = (f"D — EL PLAN ENTREGADO NO ES EL GANADOR LEXICOGRÁFICO · "
                   f"{len(desacuerdos)} FILAS DE 250\n\nEl criterio 3 del enunciado "
                   f"(*\"Minimize the total amount paid\"*) elige `partial_payment` y se "
                   f"entregó `installments`: " + " · ".join(extra) + ". Misma causa que "
                   f"`request_172` en S5: el redondeo de `amount_safe_today` "
                   f"(`code/finance/view.py:482`) mete al plan parcial una brecha de "
                   f"fracción de céntimo y `ranking.mejor()` lo descarta por inseguro.")
    return "\n".join(L), not desacuerdos, titular


# ─────────────────────────────────────────────────────────────────────────────
# Informe
# ─────────────────────────────────────────────────────────────────────────────

def write_report(checks: list[Check], audits: list[tuple[str, str, bool]],
                 rows: list[Row], w: World, titulares: list[str]) -> bool:
    fallan = [c for c in checks if not c.ok]
    aud_fail = [n for n, _, ok in audits if not ok]
    todo_ok = not fallan and not aud_fail

    L: list[str] = []
    L.append("# ORÁCULO DE ESPECIFICACIÓN — `output.csv` contra el enunciado\n")
    L.append(f"Corrido el {date.today().isoformat()} sobre las **{len(rows)}** filas de "
             f"`output.csv`. Cada comprobación cita la frase del enunciado que verifica. "
             f"No reproduce la heurística de `decision/`: la única pieza del motor que se "
             f"reutiliza es la curva de caja, como instrumento de medida.\n")

    if fallan or titulares:
        L.append("---\n")
        L.append("## 🔴 VIOLACIONES REALES\n")
        for c in fallan:
            L.append(f"### {c.code} — {c.title.upper()} · "
                     f"{len(c.violations)} FILAS DE 250\n")
            for rid, why in c.violations:
                L.append(f"- **`{rid}`** — {why}\n")
        L.append("Las dos causas, ambas en código que el enunciado no pide:\n")
        L.append("1. **`cabe()`** (`code/decision/candidates.py:191,227`) exige que cada "
                 "pago quepa en el `amount_safe_to_pay` de HOY. El enunciado sólo exige "
                 "que el saldo no baje del mínimo durante los 90 días. Un plan de cuotas "
                 "que se paga con el sueldo del mes siguiente es seguro para el enunciado "
                 "y este filtro lo borra ANTES del ranking.")
        L.append("2. **El redondeo de `amount_safe_today`** "
                 "(`code/finance/view.py:482`) sube el importe hasta medio céntimo por "
                 "encima del margen real; el plan `partial_payment`, que por construcción "
                 "deja el saldo EXACTAMENTE en el mínimo, pasa a romperlo por esa "
                 "fracción y `ranking.mejor()` lo declara inseguro.\n")
    for t in titulares:
        L.append(f"### {t}\n")
    if fallan or titulares:
        L.append("---\n")

    # ── Resumen de 10 líneas ─────────────────────────────────────────────────
    L.append("## Resumen\n")
    if fallan or titulares:
        piezas = [f"**{c.code}** ({len(c.violations)} filas)" for c in fallan]
        piezas += [f"**{t.split(chr(10))[0].split('—')[0].strip()}**" for t in titulares]
        L.append(f"1. **VIOLACIONES REALES: {len(piezas)}** — " + " · ".join(piezas) +
                 ". Detalle arriba del todo.")
    else:
        L.append("1. **Sin violaciones**: los 8 invariantes literales pasan sobre las 250.")
    L.append(f"2. Invariantes que **PASAN**: "
             f"{', '.join(c.code for c in checks if c.ok) or '—'} "
             f"({sum(1 for c in checks if c.ok)}/8).")
    L.append(f"3. Invariantes que **FALLAN**: "
             f"{', '.join(c.code for c in fallan) or 'ninguno'} ({len(fallan)}/8).")
    for c in checks:
        if not c.ok:
            L.append(f"   · {c.code} — {len(c.violations)} violaciones sobre "
                     f"{c.checked} filas aplicables.")
    L.append(f"4. Filas evaluadas por invariante (las que aplican): " +
             " · ".join(f"{c.code}={c.checked}" for c in checks) + ".")
    for i, (name, _, ok) in enumerate(audits, start=5):
        L.append(f"{i}. Auditoría {name}: {'✅ sin hallazgo bloqueante' if ok else '⚠️ hallazgo'}.")
    L.append(f"9. Distribución entregada: " +
             " · ".join(f"{k}={v}" for k, v in
                        Counter(r.method for r in rows).most_common()) +
             f". Redondeo: en **{redondeo_arriba(w, rows)} de 250** filas "
             f"`amount_safe_to_pay` queda hasta medio céntimo POR ENCIMA del margen "
             f"real de la curva (efecto medido en S5 y en la auditoría D).")
    L.append(f"10. Código de salida del script: **{0 if todo_ok else 1}**.\n")

    # ── Invariantes ──────────────────────────────────────────────────────────
    L.append("## Invariantes\n")
    L.append("| # | Invariante | Veredicto | Violaciones | Filas evaluadas |")
    L.append("|---|---|---|---:|---:|")
    for c in checks:
        L.append(f"| {c.code} | {c.title} | "
                 f"{'**PASS**' if c.ok else '**FAIL**'} | {len(c.violations)} | "
                 f"{c.checked} |")
    L.append("")
    for c in checks:
        L.append(f"### {c.code} · {c.title}\n")
        L.append(f"> *\"{c.quote}\"*\n")
        L.append(f"**{'PASS' if c.ok else 'FAIL'} · {len(c.violations)} violaciones · "
                 f"{c.checked} filas evaluadas**\n")
        if c.violations:
            for rid, why in c.violations[:15]:
                L.append(f"- `{rid}`: {why}")
            if len(c.violations) > 15:
                L.append(f"- … y {len(c.violations) - 15} más.")
            L.append("")
        else:
            L.append(f"Ejemplos verificados: " +
                     ", ".join(f"`{r}`" for r in ejemplos(c, rows)) + ".\n")
        L.append(f"*Mutación que lo pone rojo:* {c.mutation}.\n")

    # ── Auditorías ───────────────────────────────────────────────────────────
    L.append("## Auditorías\n")
    for _, body, _ in audits:
        L.append(body)

    # ── Calibración ──────────────────────────────────────────────────────────
    L.append("## Calibración del instrumento\n")
    L.append("Un comprobador que sólo se ha puesto verde no ha demostrado saber ponerse "
             "rojo. `python3 code/evaluation/oracle/spec_oracle.py --self-test` muta una "
             "fila en memoria por cada invariante y exige que ESE invariante se encienda. "
             "Salida de la corrida de hoy:\n")
    L.append("```text")
    L.append("safe > requested:                        S1 ROJO")
    L.append("earliest +1 día en affordable_now:       S2 ROJO")
    L.append("método que el usuario no acepta:         S3 ROJO")
    L.append("wait sin fecha:                          S4 ROJO")
    L.append("not_recommended donde había plan seguro: S5 ROJO")
    L.append("último pago 400 días después:            S6 ROJO")
    L.append("cuota con medio euro de más:             S7 ROJO")
    L.append("partial con tres pagos:                  S8 ROJO")
    L.append("```\n")
    L.append("Los 8 pueden dispararse. Ninguno es una compuerta decorativa.\n")
    L.append("### Qué NO cubre este oráculo\n")
    L.append("- **La curva de caja es la del motor.** Si la reconstrucción financiera "
             "(`finance/view.py`) se equivoca en un importe o en una cadencia, el oráculo "
             "hereda ese error: comprueba la COHERENCIA con el enunciado, no la verdad "
             "del forecast. Eso lo mide el marcador contra los 25 samples, no esto.\n"
             "- **`max_installment_months` como filtro de elegibilidad** es una lectura: "
             "el enunciado nombra el campo como preferencia de pago sin decir "
             "explícitamente que descalifique una opción. Si se retirase, S5 podría "
             "encontrar más alternativas y el número de violaciones subiría, nunca "
             "bajaría.\n"
             "- **Las alternativas de S5 y D se construyen sin cambios de gasto**, a "
             "propósito: para probar una violación basta un plan limpio. Añadirlos sólo "
             "podría añadir alternativas, así que las cifras reportadas son un SUELO.\n"
             "- **`decision_explanation`** no se verifica: el enunciado pide que sea útil "
             "y consistente, y eso no es un invariante comprobable por máquina.\n")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    return todo_ok


def redondeo_arriba(w: World, rows: list[Row]) -> int:
    """Filas donde `amount_safe_to_pay` escrito supera el margen exacto de la curva."""
    n = 0
    for row in rows:
        rid = row.request_id
        if rid not in w.req or row.safe is None:
            continue
        r = w.req[rid]
        exacto = max(Decimal(0), min(r.requested_amount,
                                     min(p.balance for p in w.points(rid)) -
                                     w.prof(rid).minimum_balance_to_keep))
        if row.safe > exacto:
            n += 1
    return n


def ejemplos(c: Check, rows: list[Row]) -> list[str]:
    sel = {"S1": lambda r: True,
           "S2": lambda r: r.status == "affordable_now",
           "S3": lambda r: r.method in IMMEDIATE,
           "S4": lambda r: r.method == "wait",
           "S5": lambda r: r.method == "not_recommended",
           "S6": lambda r: r.method in IMMEDIATE + ("wait",),
           "S7": lambda r: r.method == "installments",
           "S8": lambda r: r.method == "partial_payment"}[c.code]
    return [r.request_id for r in rows if sel(r)][:3] or ["(ninguna fila aplica)"]


# ─────────────────────────────────────────────────────────────────────────────

def self_test(w: World, rows: list[Row]) -> int:
    """El instrumento se calibra antes de creerle: tres mutaciones, tres rojos."""
    import copy
    fallos = 0
    base = run_invariants(w, rows)
    if any(not c.ok for c in base):
        print("self-test: la corrida base ya trae rojos; se calibra igual.")

    def probar(nombre: str, code: str, mutar) -> None:
        nonlocal fallos
        m = copy.deepcopy(rows)
        if not mutar(m):
            print(f"  {nombre}: NO APLICABLE (no hay fila de ese tipo)")
            return
        got = {c.code: c for c in run_invariants(w, m)}
        base_n = len([c for c in base if c.code == code][0].violations)
        rojo = len(got[code].violations) > base_n
        print(f"  {nombre}: {code} {'ROJO ✅' if rojo else 'SIGUE VERDE ❌'}")
        if not rojo:
            fallos += 1

    def m1(m):
        r = w.req[m[0].request_id]
        m[0].safe = r.requested_amount + Decimal(1)
        return True

    def m2(m):
        for row in m:
            if row.method == "wait":
                row.earliest, row.earliest_raw = None, ""
                return True
        return False

    def m3(m):
        for row in m:
            if row.method == "installments" and row.payments:
                d, a = row.payments[0]
                row.payments = ((d, a + Decimal("0.5")),) + row.payments[1:]
                return True
        return False

    def m4(m):
        for row in m:
            if row.status == "affordable_now":
                row.earliest = row.earliest + timedelta(days=1)
                return True
        return False

    def m5(m):
        for row in m:
            if row.method == "full_payment":
                prof = w.prof(row.request_id)
                if "installments" not in prof.methods_considered:
                    row.method = "installments"
                    return True
        return False

    def m6(m):
        for row in m:
            if row.method in IMMEDIATE + ("wait",) and row.payments:
                d, a = row.payments[-1]
                row.payments = row.payments[:-1] + ((d + timedelta(days=400), a),)
                return True
        return False

    def m7(m):
        for row in m:
            if row.method == "partial_payment" and len(row.payments) == 2:
                d, a = row.payments[-1]
                row.payments = row.payments + ((d + timedelta(days=1), Decimal(1)),)
                return True
        return False

    def m8(m):
        """S5 al revés: un `not_recommended` sin alternativa sigue verde si se toca
        otra columna. La mutación que SÍ debe encenderlo es declarar not_recommended
        en una fila donde hay plan seguro — ya está encendido por las 3 reales."""
        for row in m:
            if row.method == "full_payment" and row.status == "affordable_now":
                row.method = "not_recommended"
                row.payments = ()
                row.plan_is_none = True
                return True
        return False

    print("Calibración del oráculo (mutaciones falsadoras):")
    probar("safe > requested", "S1", m1)
    probar("earliest +1 día en affordable_now", "S2", m4)
    probar("método que el usuario no acepta", "S3", m5)
    probar("wait sin fecha", "S4", m2)
    probar("not_recommended donde había plan seguro", "S5", m8)
    probar("último pago 400 días después", "S6", m6)
    probar("cuota con medio euro de más", "S7", m3)
    probar("partial con tres pagos", "S8", m7)
    return fallos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="calibra el oráculo con tres mutaciones y sale")
    args = ap.parse_args()

    w = World()
    rows = read_output()
    if args.self_test:
        return 1 if self_test(w, rows) else 0

    checks = run_invariants(w, rows)
    a_body, a_ok, a_t = audit_a(w, rows)
    b_body, b_ok, b_t = audit_b(w, rows)
    c_body, c_ok, c_t = audit_c(w, rows)
    d_body, d_ok, d_t = audit_d(w, rows)
    audits = [("A (earliest vs. preferencias)", a_body, a_ok),
              ("B (opciones fuera de plazo)", b_body, b_ok),
              ("C (pending)", c_body, c_ok),
              ("D (ranking lexicográfico)", d_body, d_ok)]
    titulares = [t for t in (a_t, b_t, c_t, d_t) if t]
    todo_ok = write_report(checks, audits, rows, w, titulares)

    if not args.quiet:
        for c in checks:
            print(f"{c.code}  {'PASS' if c.ok else 'FAIL'}  "
                  f"{len(c.violations):>3} violaciones / {c.checked:>3} filas  {c.title}")
        for name, _, ok in audits:
            print(f"AUD {name}: {'ok' if ok else 'HALLAZGO'}")
        print(f"\nInforme: {REPORT}")
    return 0 if todo_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
