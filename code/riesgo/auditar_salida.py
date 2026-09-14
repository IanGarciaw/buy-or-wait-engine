#!/usr/bin/env python3
"""AGENTE A - auditoria estructural INDEPENDIENTE de output.csv.

No importa NADA del motor (ni contracts, ni loader, ni verifier). Las reglas se
transcriben del enunciado, no del codigo que audita. Si el motor y esta auditoria
coinciden, es porque los dos leyeron el mismo enunciado, no el mismo modulo.
"""
from __future__ import annotations

import csv, sys, re, collections
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DS = ROOT / "dataset"
TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output.csv"

COLS = ["request_id", "amount_safe_to_pay", "affordability_status",
        "recommended_payment_method", "payment_plan",
        "earliest_date_for_full_payment", "spending_changes_needed",
        "decision_explanation"]
STATUS = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHOD = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

A = []          # ANOMALIAS (imposibles si el sistema funciona)
N = []          # notas (no anomalia)


def anom(rid, tag, msg):
    A.append(f"[{tag}] {rid}: {msg}")


def rd(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def dt(s):
    s = (s or "").strip()
    if not s:
        return None
    if not DATE_RE.match(s):
        return "MAL"
    try:
        return date.fromisoformat(s)
    except ValueError:
        return "MAL"


def dec(s):
    try:
        return Decimal((s or "").strip())
    except (InvalidOperation, ValueError):
        return None


requests = {r["request_id"]: r for r in rd(DS / "requests.csv")}
profiles = {r["user_id"]: r for r in rd(DS / "financial_profiles.csv")}
events = rd(DS / "financial_events.csv")
ev_by_id = {e["event_id"]: e for e in events}
opts = collections.defaultdict(list)
for o in rd(DS / "request_payment_options.csv"):
    opts[o["request_id"]].append(o)

# ── encabezado y forma ───────────────────────────────────────────────────────
with open(TARGET, newline="", encoding="utf-8") as f:
    header = next(csv.reader(f))
rows = rd(TARGET)
if header != COLS:
    anom("<archivo>", "columns", f"encabezado != orden requerido: {header}")
if len(rows) != 250:
    anom("<archivo>", "rowcount", f"{len(rows)} filas, se esperaban 250")

ids = [r["request_id"] for r in rows]
dups = [k for k, v in collections.Counter(ids).items() if v > 1]
if dups:
    anom("<archivo>", "dup_id", f"request_id duplicados: {dups}")
faltan = set(requests) - set(ids)
sobran = set(ids) - set(requests)
if faltan:
    anom("<archivo>", "missing_id", f"faltan {len(faltan)}: {sorted(faltan)[:5]}")
if sobran:
    anom("<archivo>", "extra_id", f"sobran {len(sobran)}: {sorted(sobran)[:5]}")

st_ct, me_ct = collections.Counter(), collections.Counter()
plan_ct = collections.Counter()
floor_plan_rows = set()

for r in rows:
    rid = r["request_id"]
    req = requests.get(rid)
    if req is None:
        continue
    prof = profiles.get(req["user_id"], {})
    rd_ = dt(req["request_date"])
    dcd = dt(req["desired_completion_date"])
    requested = dec(req["requested_amount"])
    allows_partial = req["allows_partial_payment"].strip().lower() == "true"
    considers = set((prof.get("payment_methods_user_will_consider") or "").split("|")) - {""}

    # ── amount_safe_to_pay ───────────────────────────────────────────────
    safe = dec(r["amount_safe_to_pay"])
    if safe is None:
        anom(rid, "safe_nan", f"amount_safe_to_pay no numerico: {r['amount_safe_to_pay']!r}")
        safe = Decimal(0)
    if safe < 0:
        anom(rid, "safe_range", f"safe={safe} < 0")
    if safe > requested:
        anom(rid, "safe_range", f"safe={safe} > requested={requested}")

    # ── dominios ─────────────────────────────────────────────────────────
    status, method = r["affordability_status"], r["recommended_payment_method"]
    st_ct[status] += 1
    me_ct[method] += 1
    if status not in STATUS:
        anom(rid, "status_domain", f"status invalido {status!r}")
    if method not in METHOD:
        anom(rid, "method_domain", f"metodo invalido {method!r}")

    # ── earliest ─────────────────────────────────────────────────────────
    earliest = dt(r["earliest_date_for_full_payment"])
    if earliest == "MAL":
        anom(rid, "date_format", f"earliest malformada: {r['earliest_date_for_full_payment']!r}")
        earliest = None
    if status == "affordable_now" and earliest != rd_:
        anom(rid, "now_vs_earliest",
             f"affordable_now con earliest={r['earliest_date_for_full_payment']!r} != request_date={req['request_date']}")

    # ── payment_plan ─────────────────────────────────────────────────────
    plan_raw = r["payment_plan"].strip()
    payments = []
    if plan_raw != "none":
        for part in plan_raw.split("|"):
            if part.count(":") != 1:
                anom(rid, "plan_format", f"tramo mal formado: {part!r}")
                continue
            ds_, as_ = part.split(":")
            pday = dt(ds_)
            if pday in (None, "MAL"):
                anom(rid, "date_format", f"fecha de pago malformada: {ds_!r}")
                continue
            pamt = dec(as_)
            if pamt is None:
                anom(rid, "plan_format", f"monto de pago no numerico: {as_!r}")
                continue
            if pamt <= 0:
                anom(rid, "plan_amount", f"pago <= 0: {part!r}")
            payments.append((pday, pamt))
        if payments != sorted(payments, key=lambda p: p[0]):
            anom(rid, "plan_order", f"plan fuera de orden cronologico: {plan_raw}")
    plan_ct[len(payments)] += 1

    # metodo <-> plan
    if method == "not_recommended" and payments:
        anom(rid, "plan_vs_method", "not_recommended con plan de pagos")
    if method in ("full_payment", "partial_payment", "installments", "wait") and not payments:
        anom(rid, "plan_vs_method", f"{method} sin plan de pagos")

    # elegibilidad del metodo segun el perfil
    if method in ("full_payment", "partial_payment", "installments") and method not in considers:
        anom(rid, "method_eligibility",
             f"{method} no esta en payment_methods_user_will_consider={sorted(considers)}")
    if method == "wait" and "full_payment" not in considers:
        anom(rid, "method_eligibility", "wait sin que el usuario acepte full_payment")

    # full_payment
    if method == "full_payment":
        # affordable_with_plan + full_payment es legitimo SOLO si lo habilitan
        # cambios de gasto permitidos (el enunciado lo lista como via del estado).
        if status == "affordable_with_plan":
            if r["spending_changes_needed"].strip() in ("", "none"):
                anom(rid, "status_vs_method",
                     "full_payment + affordable_with_plan sin cambios de gasto que lo justifiquen")
        elif status != "affordable_now":
            anom(rid, "status_vs_method", f"full_payment con status {status}")
        if len(payments) != 1:
            anom(rid, "plan_shape", f"full_payment con {len(payments)} pagos")
        elif payments[0][0] != rd_ or payments[0][1] != requested:
            anom(rid, "plan_shape",
                 f"full_payment debe ser {req['request_date']}:{requested}, es {plan_raw}")

    # partial_payment
    if method == "partial_payment":
        if status != "affordable_with_plan":
            anom(rid, "status_vs_method", f"partial_payment con status {status}")
        if not allows_partial:
            anom(rid, "partial_not_allowed", "el request no admite pago parcial")
        if not (0 < safe < requested):
            anom(rid, "partial_range", f"partial exige 0<safe<requested; safe={safe} requested={requested}")
        if len(payments) != 2:
            anom(rid, "plan_shape", f"partial_payment con {len(payments)} pagos")
        else:
            (d1, a1), (d2, a2) = payments
            if d1 != rd_:
                anom(rid, "plan_shape", f"1er pago parcial en {d1}, no en request_date {rd_}")
            if a1 != safe:
                anom(rid, "plan_shape", f"1er pago {a1} != amount_safe_to_pay {safe}")
            if earliest and d2 != earliest:
                anom(rid, "plan_shape", f"2o pago {d2} != earliest {earliest}")
            if a1 + a2 != requested:
                anom(rid, "plan_sum", f"{a1}+{a2}={a1+a2} != requested {requested}")
            if dcd and d2 > dcd:
                anom(rid, "deadline", f"2o pago {d2} > desired_completion_date {dcd}")

    # installments -> debe coincidir EXACTAMENTE con una opcion suministrada
    if method == "installments":
        if status != "affordable_with_plan":
            anom(rid, "status_vs_method", f"installments con status {status}")
        cand = [o for o in opts.get(rid, ()) if o["payment_method"] == "installments"]
        hit = None
        for o in cand:
            n = int(o["number_of_payments"])
            amt = dec(o["payment_amount"])
            first = dt(o["first_payment_date"])
            freq = o["payment_frequency_days"].strip()
            freq = int(freq) if freq else None
            if len(payments) != n:
                continue
            exp = []
            cur = first
            from datetime import timedelta
            for k in range(n):
                exp.append((first + timedelta(days=(freq or 0) * k), amt))
            if payments == exp:
                hit = o
                break
        if hit is None:
            anom(rid, "option_match",
                 f"plan de cuotas no coincide con ninguna opcion suministrada "
                 f"({len(cand)} opciones installments). plan={plan_raw}")

    # wait
    if method == "wait":
        if status != "affordable_later":
            anom(rid, "status_vs_method", f"wait con status {status}")
        if len(payments) != 1:
            anom(rid, "plan_shape", f"wait con {len(payments)} pagos")
        elif earliest and payments[0][0] != earliest:
            anom(rid, "plan_shape", f"wait paga en {payments[0][0]} != earliest {earliest}")

    # ── spending_changes_needed ──────────────────────────────────────────
    sc = r["spending_changes_needed"].strip()
    if sc and sc != "none":
        floor_plan_rows.add(rid)
        parts = sc.split("|")
        if len(parts) > 3:
            anom(rid, "changes_count", f"{len(parts)} cambios (max 3)")
        vistos = collections.defaultdict(set)
        for p in parts:
            f = p.split(":")
            if f[0] == "stop" and len(f) == 2:
                kind, eid, newamt = "stop", f[1], None
            elif f[0] == "reduce_to" and len(f) == 3:
                kind, eid, newamt = "reduce_to", f[1], dec(f[2])
                if newamt is None:
                    anom(rid, "changes_format", f"reduce_to con monto no numerico: {p!r}")
            else:
                anom(rid, "changes_format", f"cambio mal formado: {p!r}")
                continue
            vistos[eid].add(kind)
            ev = ev_by_id.get(eid)
            if ev is None:
                anom(rid, "change_ghost", f"spending_change sobre evento inexistente: {eid}")
                continue
            if ev["user_id"] != req["user_id"]:
                anom(rid, "change_owner",
                     f"{eid} es de {ev['user_id']}, el request es de {req['user_id']}")
            flex = ev["flexibility"]
            if kind == "stop" and flex not in ("stoppable", "reducible_or_stoppable"):
                anom(rid, "change_flex", f"stop sobre {eid} con flexibility={flex}")
            if kind == "reduce_to" and flex not in ("reducible", "reducible_or_stoppable"):
                anom(rid, "change_flex", f"reduce_to sobre {eid} con flexibility={flex}")
            if ev["event_type"] not in ("expense", "subscription", "debt_payment"):
                anom(rid, "change_type", f"cambio sobre event_type={ev['event_type']} ({eid})")
            if ev["direction"] != "debit":
                anom(rid, "change_dir", f"cambio sobre direction={ev['direction']} ({eid})")
            if kind == "reduce_to" and newamt is not None:
                mn = dec(ev["minimum_allowed_amount"]) if ev["minimum_allowed_amount"].strip() else None
                cur = dec(ev["amount"]) if ev["amount"].strip() else None
                if mn is not None and newamt < mn:
                    anom(rid, "change_min", f"reduce_to {eid} a {newamt} < minimum_allowed {mn}")
                if cur is not None and newamt >= cur:
                    anom(rid, "change_noop", f"reduce_to {eid} a {newamt} >= monto actual {cur}")
        for eid, kinds in vistos.items():
            if len(kinds) > 1:
                anom(rid, "change_exclusive", f"stop y reduce_to sobre el mismo evento {eid}")
    if method == "not_recommended" and sc not in ("", "none"):
        anom(rid, "changes_vs_method", "not_recommended con cambios de gasto")
    if status == "affordable_now" and sc not in ("", "none"):
        anom(rid, "changes_vs_status", "affordable_now no puede exigir cambios de gasto")

    # ── coherencia estado <-> earliest ───────────────────────────────────
    if status == "affordable_now":
        if safe != requested:
            anom(rid, "now_vs_safe", f"affordable_now con safe={safe} != requested={requested}")
    if status == "affordable_later" and earliest is None:
        anom(rid, "later_sin_earliest", "affordable_later sin earliest_date_for_full_payment")
    if status == "not_affordable" and earliest is not None:
        anom(rid, "notaff_con_earliest",
             f"not_affordable con earliest={earliest} (deberia ir vacio)")
    if earliest is not None and rd_ is not None and earliest < rd_:
        anom(rid, "earliest_pasado", f"earliest={earliest} anterior a request_date={rd_}")

    # ── explicacion ──────────────────────────────────────────────────────
    if not r["decision_explanation"].strip():
        anom(rid, "explain_empty", "decision_explanation vacia")

print(f"archivo auditado: {TARGET}")
print(f"filas: {len(rows)} · columnas: {len(header)} · orden: {'OK' if header==COLS else 'MAL'}")
print(f"request_id: {len(set(ids))} unicos · faltan {len(faltan)} · sobran {len(sobran)}")
print("status:", dict(st_ct))
print("method:", dict(me_ct))
print("pagos por plan:", dict(sorted(plan_ct.items())))
print(f"filas con spending_changes: {len(floor_plan_rows)}")
print()
if A:
    print(f"ANOMALIAS: {len(A)}")
    for a in A:
        print("  ", a)
else:
    print("ANOMALIAS: 0 - ninguna de las clases comprobadas se disparo")
