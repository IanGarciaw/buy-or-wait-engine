"""A4 · EL MARCADOR.

Mide contra los 25 samples RESUELTOS (user_01..user_25, disjuntos de la evaluación).
El tablero dice cuánto acertamos; el clasificador por CLASE dice dónde trabajar.
No es un adorno: es la brújula. Un 12/25 no dice nada; "8 fallos de income forecast"
sí dice qué abrir primero.

Uso:
    from evaluation.scorer import score
    score(decisions)                      # list[Decision]
    python3 scorer.py predicciones.csv    # o desde un CSV con las 8 columnas

Nota sobre mensajes: este módulo lee texto de `messages.csv` SÓLO para etiquetar la
clase de un fallo (offline, sobre un sample ya resuelto). Nunca alimenta una
predicción y nunca lo toca el verificador. La defensa contra inyección vive allá.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

_CODE = Path(__file__).resolve().parent.parent
if str(_CODE) not in sys.path:
    sys.path.insert(0, str(_CODE))

from contracts import Decision, Payment, SpendingChange
import loader

CENT = Decimal("0.01")
TOL = Decimal("0.01")          # ±1% para amount_safe_to_pay

CLASSES = ("recurrence", "income forecast", "pending handling", "essential expense",
           "eligibility", "payment ranking", "currency/date",
           "message interpretation", "rounding", "other")

# Vocabulario CERRADO para etiquetar, no para decidir. Sin él, todo cae en "other".
KW_INCOME = ("salary", "payroll", "gaji", "penggajian", "income", "wage", "pay ",
             "contract has ended", "resumes", "bonus", "commission", "seasonal")
KW_PENDING = ("pending", "refund", "prize", "has not reached", "payout",
              "unrealized", "market value", "belum", "reversal")
KW_CANCEL = ("cancel", "dibatalkan", "will not", "no longer", "stopped", "ended")
KW_AMEND = ("increase", "increases", "reduced", "changed", "updated", "naik",
            "berubah", "amend", "correction", "instead")
VARIABLE_CATS = ("groceries", "transport", "dining", "shopping", "entertainment")


# ─────────────────────────────────────────────────────────────────────────────
# Normalización — comparar números, no cadenas
# ─────────────────────────────────────────────────────────────────────────────

def _dec(s) -> Decimal | None:
    if s is None:
        return None
    if isinstance(s, Decimal):
        return s
    s = str(s).strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _q(x: Decimal | None) -> Decimal | None:
    return None if x is None else Decimal(x).quantize(CENT)


def _parse_plan(s) -> tuple[tuple[date, Decimal], ...]:
    if isinstance(s, (tuple, list)):
        return tuple((p.day, _q(p.amount)) for p in s)
    s = (s or "").strip()
    if not s or s == "none":
        return ()
    out = []
    for part in s.split("|"):
        if ":" not in part:
            return (("BAD", Decimal(0)),)
        day, amt = part.split(":", 1)
        try:
            out.append((date.fromisoformat(day.strip()), _q(_dec(amt))))
        except ValueError:
            return (("BAD", Decimal(0)),)
    return tuple(out)


def _parse_changes(s) -> tuple[tuple[str, str, Decimal | None], ...]:
    if isinstance(s, (tuple, list)):
        return tuple((c.kind, c.event_id, _q(c.new_amount)) for c in s)
    s = (s or "").strip()
    if not s or s == "none":
        return ()
    out = []
    for part in s.split("|"):
        bits = part.split(":")
        if bits[0] == "stop" and len(bits) >= 2:
            out.append(("stop", bits[1], None))
        elif bits[0] == "reduce_to" and len(bits) >= 3:
            out.append(("reduce_to", bits[1], _q(_dec(bits[2]))))
        else:
            out.append(("BAD", part, None))
    return tuple(out)


def _parse_date(s) -> date | None:
    if isinstance(s, date):
        return s
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _as_pred(d) -> dict:
    """Acepta Decision o dict/fila CSV. Una sola forma interna."""
    if isinstance(d, Decision):
        return {
            "request_id": d.request_id,
            "safe": _q(_dec(d.amount_safe_to_pay)),
            "status": d.affordability_status,
            "method": d.recommended_payment_method,
            "plan": _parse_plan(d.payments),
            "earliest": _parse_date(d.earliest_date_for_full_payment),
            "changes": _parse_changes(d.spending_changes),
            "explanation": d.decision_explanation or "",
        }
    return {
        "request_id": d["request_id"],
        "safe": _q(_dec(d.get("amount_safe_to_pay"))),
        "status": (d.get("affordability_status") or "").strip(),
        "method": (d.get("recommended_payment_method") or "").strip(),
        "plan": _parse_plan(d.get("payment_plan")),
        "earliest": _parse_date(d.get("earliest_date_for_full_payment")),
        "changes": _parse_changes(d.get("spending_changes_needed")),
        "explanation": d.get("decision_explanation") or "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Contexto por usuario — para clasificar el fallo
# ─────────────────────────────────────────────────────────────────────────────

def _context(ds, req):
    ev = ds.events_by_user.get(req.user_id, ())
    msgs = ds.messages_by_user.get(req.user_id, ())
    text = " ".join(m.message_text.lower() for m in msgs
                    if (m.request_id in (None, req.request_id)))
    return {
        "has_msg": bool(msgs),
        "has_img": any(i.user_id == req.user_id for i in ds.images),
        "img_amount": any(e.amount is None for e in ev),
        "non_settled": any(e.status in ("pending", "scheduled", "cancelled",
                                        "failed", "unrealized") for e in ev),
        "foreign": any(e.currency != ds.profiles[req.user_id].home_currency
                       for e in ev),
        "variable_heavy": sum(1 for e in ev if e.category in VARIABLE_CATS) >= \
            max(1, len(ev) // 3),
        "income_ended": any("final" in (e.description or "").lower()
                            for e in ev if e.direction == "credit"),
        "kw_income": any(k in text for k in KW_INCOME),
        "kw_pending": any(k in text for k in KW_PENDING),
        "kw_cancel": any(k in text for k in KW_CANCEL),
        "kw_amend": any(k in text for k in KW_AMEND),
    }


def _classify(exp, got, ctx, req) -> tuple[str, str]:
    """Devuelve (clase, por qué). Útil > perfecto: la regla queda escrita y auditable."""
    s_ok = _safe_ok(exp["safe"], got["safe"])
    st_ok = exp["status"] == got["status"]
    m_ok = exp["method"] == got["method"]
    p_ok = exp["plan"] == got["plan"]
    e_ok = exp["earliest"] == got["earliest"]

    # 1 · El monto está bien pero la decisión no: es elegibilidad o ranking.
    if s_ok and (not st_ok or not m_ok):
        prof = None
        try:
            prof = req._prof
        except AttributeError:
            pass
        if prof is not None and got["method"] in ("full_payment", "partial_payment",
                                                  "installments") \
                and got["method"] not in prof.methods_considered:
            return "eligibility", f"método {got['method']} fuera de methods_considered"
        if {exp["method"], got["method"]} & {"wait", "not_recommended"}:
            return "eligibility", (f"esperado {exp['method']}/{exp['status']}, "
                                   f"obtenido {got['method']}/{got['status']}")
        return "payment ranking", (f"safe correcto pero método {got['method']} "
                                   f"!= {exp['method']}")

    # 2 · El monto está mal: ¿por cuánto y por qué?
    if not s_ok:
        e, g = exp["safe"] or Decimal(0), got["safe"] or Decimal(0)
        rel = abs(g - e) / abs(e) if e else (Decimal(1) if g else Decimal(0))
        if rel <= Decimal("0.05"):
            return "rounding", f"safe difiere {rel:.2%} (<=5%)"
        if ctx["img_amount"] or ctx["has_img"]:
            return "message interpretation", ("hay un monto que sólo existe en una "
                                              "imagen: extracción o su ausencia")
        if ctx["kw_income"] or ctx["income_ended"]:
            return "income forecast", ("el usuario tiene evidencia de cambio/fin de "
                                       "ingreso y el monto se va lejos")
        if ctx["kw_pending"] or ctx["non_settled"]:
            return "pending handling", ("hay pendientes/no liquidados; un crédito "
                                        "pendiente contado infla el saldo")
        if ctx["kw_cancel"] or ctx["kw_amend"]:
            return "message interpretation", "mensaje de cancelación/enmienda sin aplicar"
        if ctx["foreign"]:
            return "currency/date", "el usuario tiene eventos en moneda extranjera"
        if g > e:
            return ("essential expense" if ctx["variable_heavy"] else "recurrence",
                    "somos más optimistas: falta gasto proyectado")
        return "recurrence", "somos más pesimistas: sobra gasto proyectado"

    # 3 · Monto, estado y método bien; falla el plan.
    if not p_ok:
        if len(exp["plan"]) == len(got["plan"]) and \
                [d for d, _ in exp["plan"]] == [d for d, _ in got["plan"]]:
            return "rounding", "mismas fechas, montos distintos"
        return "payment ranking", "el plan elegido no es el del ground truth"

    # 4 · Sólo falla la fecha más temprana.
    if not e_ok:
        if exp["earliest"] is None or got["earliest"] is None:
            return "eligibility", (f"earliest esperado={exp['earliest']} "
                                   f"obtenido={got['earliest']}")
        return "income forecast", (f"earliest desfasado "
                                   f"{(got['earliest'] - exp['earliest']).days} días")

    if exp["changes"] != got["changes"]:
        return "eligibility", "spending_changes distintos"
    return "other", "difiere en algo que el tablero no mide"


def _safe_ok(exp: Decimal | None, got: Decimal | None) -> bool:
    if exp is None or got is None:
        return exp is got
    if exp == 0:
        return got == 0
    return abs(got - exp) <= abs(exp) * TOL


# ─────────────────────────────────────────────────────────────────────────────
# EL MARCADOR
# ─────────────────────────────────────────────────────────────────────────────

def score(decisions: list, verbose: bool = True) -> dict:
    samples = loader.load_samples()
    ds = loader.load()
    reqs = {r.request_id: r for r in loader.sample_requests()}
    for r in reqs.values():
        object.__setattr__(r, "_prof", ds.profiles.get(r.user_id))

    got_by_id = {}
    for d in (decisions or []):
        p = _as_pred(d)
        got_by_id[p["request_id"]] = p

    n = len(samples)
    hits = Counter()
    rows, classes, missing = [], Counter(), []

    for s in samples:
        rid = s["request_id"]
        exp = _as_pred(s)
        got = got_by_id.get(rid)
        if got is None:
            missing.append(rid)
            classes["other"] += 1
            rows.append({"request_id": rid, "fields": ["MISSING"],
                         "class": "other", "why": "no hay predicción para esta fila"})
            continue
        f = {
            "STATUS": exp["status"] == got["status"],
            "SAFE": _safe_ok(exp["safe"], got["safe"]),
            "METHOD": exp["method"] == got["method"],
            "PLAN": exp["plan"] == got["plan"],
            "EARLIEST": exp["earliest"] == got["earliest"],
        }
        for k, v in f.items():
            if v:
                hits[k] += 1
        if exp["changes"] == got["changes"]:
            hits["CHANGES"] += 1
        if all(f.values()) and exp["changes"] == got["changes"]:
            hits["EXACT"] += 1
            continue
        cls, why = _classify(exp, got, _context(ds, reqs[rid]), reqs[rid])
        classes[cls] += 1
        rows.append({"request_id": rid,
                     "fields": [k for k, v in f.items() if not v] +
                               ([] if exp["changes"] == got["changes"] else ["CHANGES"]),
                     "class": cls, "why": why,
                     "exp": exp, "got": got})

    out = {
        "n": n,
        "status": hits["STATUS"], "safe": hits["SAFE"], "method": hits["METHOD"],
        "plan": hits["PLAN"], "earliest": hits["EARLIEST"],
        "changes": hits["CHANGES"], "exact": hits["EXACT"],
        "classes": dict(classes), "failures": rows, "missing": missing,
    }
    if verbose:
        _print_board(out)
    return out


def _print_board(r: dict) -> None:
    n = r["n"]
    print(f"{'STATUS:':<15}{r['status']} / {n}")
    print(f"{'SAFE ±1%:':<15}{r['safe']} / {n}")
    print(f"{'METHOD:':<15}{r['method']} / {n}")
    print(f"{'PLAN:':<15}{r['plan']} / {n}")
    print(f"{'EARLIEST:':<15}{r['earliest']} / {n}")
    print()
    print(f"{'CHANGES:':<15}{r['changes']} / {n}    (no puntúa en el tablero, "
          f"sí en la evaluación)")
    print(f"{'FILAS EXACTAS:':<15}{r['exact']} / {n}")
    if r["missing"]:
        print(f"SIN PREDICCIÓN: {len(r['missing'])} -> {', '.join(r['missing'][:8])}")
    print()
    print("FALLOS POR CLASE (dónde trabajar primero)")
    if not r["classes"]:
        print("  — ninguno —")
    for cls, k in sorted(r["classes"].items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {k:>3}  {cls}")
    print()
    print("DETALLE")
    for row in r["failures"]:
        print(f"  {row['request_id']}  [{row['class']}]  "
              f"falla: {','.join(row['fields'])}")
        print(f"        {row['why']}")
        if "exp" in row:
            e, g = row["exp"], row["got"]
            if not _safe_ok(e["safe"], g["safe"]):
                print(f"        safe   esperado={e['safe']}  obtenido={g['safe']}")
            if e["status"] != g["status"] or e["method"] != g["method"]:
                print(f"        deci   esperado={e['status']}/{e['method']}  "
                      f"obtenido={g['status']}/{g['method']}")
            if e["plan"] != g["plan"]:
                print(f"        plan   esperado={_show(e['plan'])}")
                print(f"               obtenido={_show(g['plan'])}")
            if e["earliest"] != g["earliest"]:
                print(f"        early  esperado={e['earliest']}  "
                      f"obtenido={g['earliest']}")
            if e["changes"] != g["changes"]:
                print(f"        chg    esperado={e['changes']}  obtenido={g['changes']}")


def _show(plan) -> str:
    return "|".join(f"{d}:{a}" for d, a in plan) if plan else "none"


def format_audit(verbose: bool = True) -> dict:
    """¿Si el motor acertara la decisión EXACTA, el CSV saldría idéntico?

    No es una pregunta retórica. El ground truth usa DOS convenciones distintas:
      · amount_safe_to_pay  -> sin ceros finales ('603.3', '17229139.2')
      · payment_plan y spending_changes_needed -> 2 decimales si hay fracción
        ('620.40', '996.60', '23.50') y entero pelado si no ('25256', '68432')
    `contracts.fmt_amount` aplica la primera a las tres columnas. Eso rompe las
    otras dos. Se mide, no se opina.
    """
    import contracts as C
    from verifier import ground_truth_decisions
    gt = {d.request_id: d for d in ground_truth_decisions()}
    cols = ("amount_safe_to_pay", "payment_plan", "earliest_date_for_full_payment",
            "spending_changes_needed")
    bad = []
    for s_ in loader.load_samples():
        row = C.to_row(gt[s_["request_id"]])
        for c in cols:
            if row[c] != (s_[c] or ""):
                bad.append((s_["request_id"], c, s_[c], row[c]))
    if verbose:
        print(f"AUDITORÍA DE FORMATO · filas del ground truth que el sistema "
              f"NO reproduciría carácter a carácter: {len(bad)}")
        for rid, c, exp, got in bad:
            print(f"  {rid:12} {c:32} ground truth={exp!r}  to_row()={got!r}")
        if bad:
            print("  -> contracts.fmt_amount necesita una segunda forma para "
                  "payment_plan y spending_changes_needed (2 decimales si hay "
                  "fracción). Sólo A0 puede tocar contracts.py.")
    return {"mismatches": bad}


def decisions_from_csv(path: str | Path) -> list[dict]:
    """Permite marcar un output.csv sin construir Decisions."""
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def score_ground_truth() -> dict:
    """Calibración del marcador: contra sí mismo debe dar 25/25 en todo.
    Un marcador que no sabe reconocer la respuesta correcta no mide nada."""
    return score(loader.load_samples(), verbose=False)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        score(decisions_from_csv(sys.argv[1]))
    else:
        r = score_ground_truth()
        ok = all(r[k] == r["n"] for k in
                 ("status", "safe", "method", "plan", "earliest", "changes"))
        print("Autocomprobación del marcador contra el propio ground truth:")
        _print_board(r)
        print("VERDE: el marcador reconoce la respuesta correcta." if ok
              else "ROJO: el marcador no reconoce ni su propio ground truth.")
        print()
        format_audit()
        sys.exit(0 if ok else 1)
