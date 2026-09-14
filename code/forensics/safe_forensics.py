#!/usr/bin/env python3
"""FRENTE A · FORENSE DE `amount_safe_to_pay`.

No corrige nada. Sólo LEE. Convierte cada delta contra la verdad de campo en
ARITMÉTICA: qué evento, qué serie, cuántas veces.

Instrumento central:

    safe = min(requested, max(0, trough_90d - minimum_balance_to_keep))
    =>  trough implícito por la VERDAD = GT_safe + minimum_balance_to_keep
    =>  nuestro trough                 = our_safe + minimum_balance_to_keep

Cuando `safe == requested` el tope OCULTA el valle: en ese caso sólo se sabe que
el valle fue >= objetivo. Esos casos se marcan `capped` y no se mezclan.

    python3 code/forensics/safe_forensics.py            # fichas + tabla + informe
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent          # code/forensics
CODE = HERE.parent                              # code/
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

import loader                                            # noqa: E402
from contracts import Event, Request                     # noqa: E402
from finance import params                               # noqa: E402
from finance.view import _home, build_view, curve, reconstruct  # noqa: E402
from finance.series import occurrences                   # noqa: E402

D = Decimal
CENT = D("0.01")
TOL = D("0.01")            # ±1 % para declarar coincidencia


# ─────────────────────────────────────────────────────────────────────────────
# utilidades
# ─────────────────────────────────────────────────────────────────────────────

def _dec(s):
    s = (str(s) if s is not None else "").strip().replace(",", "")
    if not s:
        return None
    try:
        return D(s)
    except Exception:
        return None


def _date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def m(x) -> str:
    """Número legible con separador de miles."""
    if x is None:
        return "—"
    q = D(x).quantize(CENT)
    return f"{q:,.2f}"


def pct(a, b):
    if b is None or b == 0:
        return None
    return (D(a) / D(b) * 100)


def close(a, b, tol=TOL) -> bool:
    a, b = abs(D(a)), abs(D(b))
    if b == 0:
        return a == 0
    return abs(a - b) / b <= tol


# ─────────────────────────────────────────────────────────────────────────────
# candidatos numéricos: todo importe que EXISTE en el mundo del usuario
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Cand:
    label: str
    amount: Decimal
    kind: str          # event | serie | serieN | perfil | pedido | hecho
    meta: dict = field(default_factory=dict)


def candidatos(ds, req, rec, facts) -> list[Cand]:
    prof = ds.profiles[req.user_id]
    out: list[Cand] = []
    img = (facts or {}).get("images") or {}

    for e in ds.events_by_user.get(req.user_id, ()):
        amt = e.amount
        origen = "csv"
        if amt is None:
            f = img.get(e.event_id)
            if f is not None and getattr(f, "amount", None) is not None:
                amt, origen = f.amount, "imagen"
        if amt is None:
            continue
        when = e.settlement_date or e.event_date or req.request_date
        home = _home(abs(D(amt)), e, prof, when, ds)
        fx = e.currency != prof.home_currency
        for n in (1, 2, 3, 4):
            out.append(Cand(
                (f"{n}x " if n > 1 else "")
                + f"{e.event_id} {e.event_type}/{e.category} {e.direction} "
                  f"{e.status} {when} \"{e.description[:38]}\"",
                home * n, "event" if n == 1 else "eventN",
                {"event_id": e.event_id, "dir": e.direction, "status": e.status,
                 "type": e.event_type, "fx": fx, "origen": origen, "n": n,
                 "cat": e.category, "day": when}))

    for s in rec.series:
        n_occ = len(occurrences(s, rec.start, rec.end, rec.start))
        for n in range(1, 9):
            out.append(Cand(
                f"{n}x serie {s.direction}/{s.event_type}/{s.category} "
                f"({s.period_kind}:{s.period}, muestra {s.sample.event_id}, "
                f"{n_occ} ocurrencias en 90d)",
                s.amount * n, "serie" if n == 1 else "serieN",
                {"n": n, "dir": s.direction, "cat": s.category,
                 "event_id": s.sample.event_id, "type": s.event_type,
                 "n_occ": n_occ, "unit": s.amount}))

    out.append(Cand("profile.current_available_balance",
                    prof.current_available_balance, "perfil", {}))
    out.append(Cand("profile.minimum_balance_to_keep",
                    prof.minimum_balance_to_keep, "perfil", {}))
    out.append(Cand("request.requested_amount", req.requested_amount, "pedido", {}))
    out.append(Cand("headroom inicial (balance - minimo)",
                    prof.current_available_balance - prof.minimum_balance_to_keep,
                    "perfil", {}))
    return out


def cercanos(delta, cands, top=5):
    """Los importes MÁS PARECIDOS al delta aunque no entren en el ±1 %."""
    if delta is None or delta == 0:
        return []
    out = []
    for c in cands:
        if c.amount is None or c.amount == 0:
            continue
        err = abs(abs(D(delta)) - abs(D(c.amount))) / abs(D(c.amount))
        out.append((c, err))
    out.sort(key=lambda t: t[1])
    return out[:top]


def coincidencias(delta: Decimal, cands: list[Cand], top=6) -> list[tuple[Cand, Decimal]]:
    if delta is None or delta == 0:
        return []
    hits = []
    for c in cands:
        if c.amount is None or c.amount == 0:
            continue
        err = abs(abs(D(delta)) - abs(D(c.amount))) / abs(D(c.amount))
        if err <= TOL:
            hits.append((c, err))
    hits.sort(key=lambda t: (t[1], t[0].kind != "event", abs(t[0].amount)))
    return hits[:top]


# ─────────────────────────────────────────────────────────────────────────────
# una ficha
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Ficha:
    rid: str
    uid: str
    req: Request
    gt_safe: Decimal
    our_safe: Decimal
    our_view_safe: Decimal
    delta: Decimal
    delta_pct: Decimal | None
    balance: Decimal
    minimo: Decimal
    headroom0: Decimal
    our_trough: Decimal
    our_trough_day: date
    gt_trough: Decimal
    trough_delta: Decimal
    gt_capped: bool
    our_capped: bool
    valle: list
    ingresos: Decimal
    gastos: Decimal
    n_proj: int
    n_conf: int
    notas: list
    gt_earliest: date | None
    our_earliest: date | None
    earliest_ok: bool
    hits: list
    cerca: list
    frontera: dict
    perillas: list = field(default_factory=list)
    pasa1: bool = False
    bucket: str = "J"
    razon: str = ""


def analizar(ds, facts, req, gt_row, decide_fn, verify_fn) -> Ficha:
    prof = ds.profiles[req.user_id]
    view = build_view(ds, req, facts)
    rec = reconstruct(ds, req, facts)
    pts, lo, lo_day = curve(rec)

    our_view_safe = D(view.amount_safe_today)
    our_safe = our_view_safe
    our_earliest = view.earliest_full_payment
    if decide_fn is not None:
        try:
            dec = decide_fn(view, req, ds.options_by_request.get(req.request_id, ()))
            if verify_fn is not None:
                try:
                    dec = verify_fn(dec, req, ds, facts).decision
                except TypeError:
                    dec = verify_fn(dec, req, ds).decision
            our_safe = D(dec.amount_safe_to_pay)
            our_earliest = dec.earliest_date_for_full_payment
        except Exception as exc:                      # el forense nunca revienta
            pass

    gt_safe = _dec(gt_row.get("amount_safe_to_pay")) or D(0)
    delta = gt_safe - our_safe
    dpct = pct(delta, gt_safe) if gt_safe else None

    gt_capped = close(gt_safe, req.requested_amount, D("0.0001"))
    our_capped = close(our_safe, req.requested_amount, D("0.0001"))

    gt_trough = gt_safe + prof.minimum_balance_to_keep
    our_trough = our_safe + prof.minimum_balance_to_keep
    trough_delta = gt_trough - our_trough

    # flujos que forman el valle: todo lo que ocurre entre el día 0 y el mínimo
    valle = []
    saldo = prof.current_available_balance
    for f in sorted(rec.flows, key=lambda f: (f.day, f.note)):
        if f.day > lo_day:
            break
        saldo += f.delta
        valle.append((f.day, f.event_id or "—", f.note, f.delta, saldo))

    ingresos = sum((f.delta for f in rec.flows if f.delta > 0), D(0))
    gastos = sum((-f.delta for f in rec.flows if f.delta < 0), D(0))
    n_proj = sum(1 for f in rec.flows if f.note.startswith("recurrente"))
    n_conf = len(rec.flows) - n_proj

    gt_earliest = _date(gt_row.get("earliest_date_for_full_payment"))
    earliest_ok = (gt_earliest == our_earliest)

    cands = candidatos(ds, req, rec, facts)
    hits = coincidencias(delta, cands)
    cerca = cercanos(delta, cands)
    pasa1 = close(our_safe, gt_safe) if gt_safe else (our_safe == gt_safe)

    return Ficha(
        rid=req.request_id, uid=req.user_id, req=req,
        gt_safe=gt_safe, our_safe=our_safe, our_view_safe=our_view_safe,
        delta=delta, delta_pct=dpct,
        balance=prof.current_available_balance, minimo=prof.minimum_balance_to_keep,
        headroom0=prof.current_available_balance - prof.minimum_balance_to_keep,
        our_trough=our_trough, our_trough_day=lo_day,
        gt_trough=gt_trough, trough_delta=trough_delta,
        gt_capped=gt_capped, our_capped=our_capped,
        valle=valle, ingresos=ingresos, gastos=gastos,
        n_proj=n_proj, n_conf=n_conf, notas=list(rec.notes),
        gt_earliest=gt_earliest, our_earliest=our_earliest, earliest_ok=earliest_ok,
        hits=hits, cerca=cerca, frontera={}, pasa1=pasa1)


# ─────────────────────────────────────────────────────────────────────────────
# experimento de FRONTERA (bucket F): ¿el delta se explica moviendo el borde?
# ─────────────────────────────────────────────────────────────────────────────

def frontera(ds, facts, req, gt_safe) -> dict:
    """Recalcula `safe` con el borde movido. Monkeypatch EN MEMORIA de params."""
    original = (params.HORIZON, params.INCLUDE_DAY_ZERO)
    out = {}
    variantes = [("dia0=off", 90, False), ("h=89", 89, True),
                 ("h=91", 91, True), ("h=89,dia0=off", 89, False)]
    try:
        for nombre, h, dz in variantes:
            params.HORIZON, params.INCLUDE_DAY_ZERO = h, dz
            try:
                v = build_view(ds, req, facts)
                s = D(v.amount_safe_today)
            except Exception:
                continue
            out[nombre] = s
            if close(s, gt_safe):
                out["EXPLICA"] = nombre
    finally:
        params.HORIZON, params.INCLUDE_DAY_ZERO = original
    return out


# Qué REGLA, cambiada sola, reproduce la verdad de campo. Cada entrada es
# (nombre, atributo de params, valor). Nada de esto se escribe a disco: el
# monkeypatch vive en memoria y se revierte siempre.
PERILLAS = [
    ("estimador=mean", "ESTIMATOR", "mean"),
    ("estimador=last", "ESTIMATOR", "last"),
    ("estimador=p75", "ESTIMATOR", "p75"),
    ("estimador=max", "ESTIMATOR", "max"),
    ("var_mult=1.00", "VARIABLE_MULT", 1.00),
    ("var_mult=1.15", "VARIABLE_MULT", 1.15),
    ("var_mult=1.30", "VARIABLE_MULT", 1.30),
    ("var_mult=0.90", "VARIABLE_MULT", 0.90),
    ("min_occ=2", "MIN_OCCURRENCES", 2),
    ("min_occ=4", "MIN_OCCURRENCES", 4),
    ("min_occ_income=3", "MIN_OCCURRENCES_INCOME", 3),
    ("lookback=3", "LOOKBACK", 3),
    ("lookback=12", "LOOKBACK", 12),
    ("income_est=mean", "INCOME_ESTIMATOR", "mean"),
    ("stale=0.9", "STALE_FACTOR", 0.9),
    ("stale=2.0", "STALE_FACTOR", 2.0),
    ("skip_reciente=0.5", "SKIP_IF_RECENT_FRAC", 0.5),
    ("nuevo_recurrente=ignorar", "FACT_NUEVO_RECURRENTE", "ignorar"),
    ("nuevo_recurrente=ingreso", "FACT_NUEVO_RECURRENTE", "ingreso"),
    ("not_yet_cash=ignorar", "FACT_NOT_YET_CASH_SIN_OBJETIVO", "ignorar"),
    ("not_yet_cash=suprimir_ingreso", "FACT_NOT_YET_CASH_SIN_OBJETIVO",
     "suprimir_ingreso"),
    ("enmienda_sin_objetivo=off", "FACT_ENMIENDA_SIN_OBJETIVO_ES_INGRESO", False),
    ("ingreso_terminado=terminar", "FACT_INGRESO_TERMINADO_CON_OBJETIVO",
     "terminar_ingreso"),
    ("SIN HECHOS", "__nofacts__", None),
]


def perillas(ds, facts, req, gt_safe) -> list[str]:
    """¿Qué cambio de UNA regla reproduce el GT dentro de ±1 %?"""
    explican = []
    for nombre, attr, val in PERILLAS:
        if attr == "__nofacts__":
            try:
                v = build_view(ds, req, {"images": {}, "messages": {}})
                if close(D(v.amount_safe_today), gt_safe):
                    explican.append(nombre)
            except Exception:
                pass
            continue
        if not hasattr(params, attr):
            continue
        old = getattr(params, attr)
        try:
            setattr(params, attr, val)
            v = build_view(ds, req, facts)
            if close(D(v.amount_safe_today), gt_safe):
                explican.append(nombre)
        except Exception:
            pass
        finally:
            setattr(params, attr, old)
    return explican


# ─────────────────────────────────────────────────────────────────────────────
# clasificación en cubos
# ─────────────────────────────────────────────────────────────────────────────

BUCKETS = {
    "A": "un evento concreto",
    "B": "una recurrencia concreta (1x)",
    "C": "N x una recurrencia",
    "D": "un ingreso",
    "E": "conversión de moneda",
    "F": "frontera de fecha (día 0 / día 90)",
    "G": "deduplicación",
    "H": "status del evento (pending/scheduled/settled)",
    "I": "mensaje o imagen",
    "J": "desconocido",
}


def clasificar(f: Ficha) -> tuple[str, str]:
    if f.delta == 0:
        return ("=", "clavado")
    if f.frontera.get("EXPLICA"):
        return ("F", f"frontera: {f.frontera['EXPLICA']} reproduce el GT")
    if not f.hits:
        return _por_perilla(f, "ningún importe del usuario coincide ±1 % con el delta")

    c, err = f.hits[0]
    meta = c.meta
    detalle = f"{m(c.amount)} — {c.label}"

    if c.kind == "event":
        if meta.get("origen") == "imagen":
            return ("I", f"= importe leído de imagen · {detalle}")
        if meta.get("fx"):
            return ("E", f"= evento en moneda extranjera · {detalle}")
        if meta.get("dir") == "credit" or meta.get("type") == "income":
            return ("D", f"= 1 ingreso · {detalle}")
        if meta.get("status") in ("pending", "scheduled"):
            return ("H", f"= evento {meta['status']} · {detalle}")
        return ("A", f"= 1 evento · {detalle}")
    if c.kind == "eventN":
        if meta.get("dir") == "credit" or meta.get("type") == "income":
            return ("D", f"= {meta['n']} x un ingreso · {detalle}")
        return ("C", f"= {meta['n']} x el evento {meta['event_id']} "
                     f"({meta.get('cat')}) · {detalle}")
    if c.kind == "serie":
        if meta.get("dir") == "credit":
            return ("D", f"= 1 ocurrencia de ingreso recurrente · {detalle}")
        return ("B", f"= 1 x recurrencia · {detalle}")
    if c.kind == "serieN":
        if meta.get("dir") == "credit":
            return ("D", f"= {meta['n']} x ingreso recurrente · {detalle}")
        return ("C", f"= {meta['n']} x recurrencia · {detalle}")
    return _por_perilla(f, f"coincide con {detalle} (no atribuible a evento/serie)")


PERILLA_BUCKET = [
    (("income_est", "min_occ_income", "stale"), "D",
     "nivel/vida de la serie de INGRESO"),
    (("nuevo_recurrente", "not_yet_cash", "enmienda", "SIN HECHOS",
      "ingreso_terminado"), "I", "interpretación de un mensaje"),
    (("estimador", "var_mult", "lookback", "min_occ", "skip_reciente"), "C",
     "nivel del GASTO recurrente acumulado en 90 d"),
]


def _por_perilla(f: Ficha, fallback: str) -> tuple[str, str]:
    for k in f.perillas:
        for claves, cubo, texto in PERILLA_BUCKET:
            if any(c in k for c in claves):
                return (cubo, f"reproducido al cambiar UNA regla: `{k}` -> {texto}")
    extra = ""
    if f.cerca:
        c, err = f.cerca[0]
        extra = f" · lo más parecido: {m(c.amount)} a {err*100:.1f}% ({c.label[:60]})"
    return ("J", fallback + (f" · perillas: {f.perillas}" if f.perillas
                             else " · ninguna perilla lo reproduce") + extra)


# ─────────────────────────────────────────────────────────────────────────────
# informe
# ─────────────────────────────────────────────────────────────────────────────

def ficha_md(f: Ficha) -> str:
    L = []
    ok = "CLAVADO" if f.delta == 0 else f"DELTA {'+' if f.delta > 0 else ''}{m(f.delta)}"
    L.append(f"### {f.rid} · {f.uid} · {ok}")
    L.append("")
    L.append(f"- pedido: {m(f.req.requested_amount)} · fecha {f.req.request_date} "
             f"· tipo {f.req.request_type} · parcial={f.req.allows_partial_payment}")
    L.append(f"- GT_safe **{m(f.gt_safe)}**{'  (= requested, CAPADO)' if f.gt_capped else ''}"
             f" · our_safe **{m(f.our_safe)}**"
             f"{'  (= requested, CAPADO)' if f.our_capped else ''}"
             f" · delta {m(f.delta)}"
             f" · delta_pct {('%.2f%%' % f.delta_pct) if f.delta_pct is not None else '—'}")
    if f.our_view_safe != f.our_safe:
        L.append(f"- OJO: finance dio {m(f.our_view_safe)} y la decisión/verificador "
                 f"lo dejó en {m(f.our_safe)}")
    L.append(f"- balance {m(f.balance)} · minimo {m(f.minimo)} · "
             f"headroom inicial {m(f.headroom0)}")
    cap = ""
    if f.gt_capped:
        cap = " (LÍMITE INFERIOR: el tope oculta el valle real)"
    L.append(f"- our_trough **{m(f.our_trough)}** @ {f.our_trough_day} · "
             f"GT_implied_trough **{m(f.gt_trough)}**{cap} · "
             f"trough_delta **{m(f.trough_delta)}**")
    L.append(f"- ingresos proyectados +{m(f.ingresos)} · gastos proyectados -{m(f.gastos)} "
             f"· flujos confirmados {f.n_conf} · ocurrencias recurrentes {f.n_proj}")
    L.append(f"- earliest GT {f.gt_earliest or '(vacío)'} · nuestro "
             f"{f.our_earliest or '(vacío)'} · **{'CORRECTO' if f.earliest_ok else 'MAL'}**")
    hechos = [n for n in f.notas if not n.startswith(("flex:", "series=", "flujos=", "min="))]
    L.append(f"- hechos aplicados ({len(hechos)}): "
             + ("; ".join(hechos[:8]) if hechos else "ninguno"))
    if f.frontera:
        L.append("- frontera: " + " · ".join(
            f"{k}={m(v) if isinstance(v, Decimal) else v}" for k, v in f.frontera.items()))
    L.append("")
    L.append(f"**Valle** (flujos del día 0 al mínimo {f.our_trough_day}):")
    L.append("")
    L.append("| fecha | event_id | qué | dirección | monto | saldo |")
    L.append("|---|---|---|---|---:|---:|")
    v = f.valle
    muestra = v if len(v) <= 40 else (v[:20] + [("…", "…", f"… {len(v)-40} flujos omitidos …",
                                                 D(0), D(0))] + v[-20:])
    for day, eid, nota, delta, saldo in muestra:
        if eid == "…":
            L.append(f"| … | … | {nota} | | | |")
            continue
        L.append(f"| {day} | {eid} | {nota[:46]} | "
                 f"{'+' if delta > 0 else '-'} | {m(abs(delta))} | {m(saldo)} |")
    L.append("")
    if f.perillas:
        L.append(f"**Perillas que reproducen el GT** (una sola, ±1 %): "
                 + ", ".join(f"`{k}`" for k in f.perillas))
        L.append("")
    if not f.pasa1:
        L.append(f"**Coincidencias numéricas del delta {m(f.delta)} (±1 %)**:")
        L.append("")
        if not f.hits:
            L.append("- ninguna dentro del ±1 %. Lo MÁS parecido que existe en el "
                     "mundo del usuario:")
            for c, err in f.cerca:
                L.append(f"  - `{m(c.amount)}` a {err*100:.1f}% — {c.label}")
        for c, err in f.hits:
            L.append(f"- `{m(c.amount)}` err {err*100:.2f}% — {c.label}")
        L.append("")
        L.append(f"**Cubo {f.bucket}** · {f.razon}")
    L.append("")
    return "\n".join(L)


def main():
    ds = loader.load()
    try:
        from extraction.facts import load_facts
        facts = load_facts(ds)
    except Exception as exc:
        print(f"AVISO: sin hechos ({exc})")
        facts = {"images": {}, "messages": {}}

    try:
        from decision.engine import decide as decide_fn
    except Exception:
        decide_fn = None
    try:
        from evaluation.verifier import verify as verify_fn
    except Exception:
        verify_fn = None

    gt = {r["request_id"]: r for r in loader.load_samples()}
    reqs = loader.sample_requests()

    fichas = []
    for req in reqs:
        f = analizar(ds, facts, req, gt[req.request_id], decide_fn, verify_fn)
        if not f.pasa1:
            f.frontera = frontera(ds, facts, req, f.gt_safe)
            f.perillas = perillas(ds, facts, req, f.gt_safe)
        f.bucket, f.razon = clasificar(f)
        fichas.append(f)

    (HERE / "FICHAS.md").write_text(
        "# FICHAS · los 25 samples, uno por uno\n\n"
        "Generado por `code/forensics/safe_forensics.py`. Sólo lectura del dataset.\n\n"
        + "\n".join(ficha_md(f) for f in fichas), encoding="utf-8")

    # tabla de deltas
    malos = [f for f in fichas if not f.pasa1]
    buenos = [f for f in fichas if f.pasa1]
    filas = []
    for f in malos:
        filas.append(f"| {f.rid} | {m(f.gt_safe)} | {m(f.our_safe)} | "
                     f"{'+' if f.delta > 0 else ''}{m(f.delta)} | "
                     f"{('%.1f%%' % f.delta_pct) if f.delta_pct is not None else '—'} | "
                     f"{f.bucket} | {f.razon} |")
    tabla = ("| request | GT_safe | our_safe | delta | delta% | cubo | aritmética |\n"
             "|---|---:|---:|---:|---:|:--:|---|\n" + "\n".join(filas))

    # asimetría earliest
    grupo_a = [f for f in malos if f.earliest_ok]
    grupo_b = [f for f in malos if not f.earliest_ok]

    # controles positivos
    ctrl = []
    for f in buenos:
        hechos = [n for n in f.notas
                  if not n.startswith(("flex:", "series=", "flujos=", "min="))]
        ctrl.append({
            "rid": f.rid, "capped": f.our_capped, "hechos": len(hechos),
            "earliest_ok": f.earliest_ok, "trough": f.our_trough,
            "n_proj": f.n_proj, "n_conf": f.n_conf,
        })

    resumen = {
        "clavados": [f.rid for f in buenos],
        "fallos": len(malos),
        "cubos": {},
        "grupo_a_safe_mal_earliest_bien": [f.rid for f in grupo_a],
        "grupo_b_safe_mal_earliest_mal": [f.rid for f in grupo_b],
        "controles": ctrl,
    }
    for f in malos:
        resumen["cubos"].setdefault(f.bucket, []).append(f.rid)

    (HERE / "TABLA.md").write_text(
        "# TABLA DE DELTAS\n\n" + tabla + "\n\n## Reparto por cubo\n\n"
        + "\n".join(f"- **{k} · {BUCKETS[k]}** ({len(v)}): {', '.join(v)}"
                    for k, v in sorted(resumen["cubos"].items()))
        + "\n\n## Asimetría earliest\n\n"
        + f"- (a) safe MAL + earliest BIEN ({len(grupo_a)}): "
        + ", ".join(f.rid for f in grupo_a)
        + f"\n- (b) safe MAL + earliest MAL ({len(grupo_b)}): "
        + ", ".join(f.rid for f in grupo_b) + "\n", encoding="utf-8")

    # contraste correctos vs fallos — las columnas que separan (o no) un grupo del otro
    contraste = []
    for f in fichas:
        ev = ds.events_by_user.get(f.uid, ())
        prof = ds.profiles[f.uid]
        hechos = [n for n in f.notas
                  if not n.startswith(("flex:", "series=", "flujos=", "min="))]
        contraste.append({
            "rid": f.rid, "ok_1pct": f.pasa1, "gt_capado": f.gt_capped,
            "delta_pct": (float(f.delta_pct) if f.delta_pct is not None else None),
            "moneda": prof.home_currency,
            "eventos_fx": sum(1 for e in ev if e.currency != prof.home_currency),
            "pending": sum(1 for e in ev if e.status == "pending"),
            "scheduled": sum(1 for e in ev if e.status == "scheduled"),
            "mensajes": len(ds.messages_by_user.get(f.uid, ())),
            "imagenes": sum(1 for i in ds.images if i.user_id == f.uid),
            "hechos": len(hechos), "series": f.n_proj and None or None,
            "dia_del_valle": (f.our_trough_day - f.req.request_date).days,
            "earliest_ok": f.earliest_ok,
        })
    (HERE / "controles.json").write_text(
        json.dumps(contraste, indent=1, default=str), encoding="utf-8")
    g_ok = [c for c in contraste if c["ok_1pct"]]
    g_no = [c for c in contraste if not c["ok_1pct"]]
    def _avg(rs, k):
        return sum(r[k] for r in rs) / len(rs) if rs else 0
    print("\ncontraste correctos(6) vs fallos(19):")
    for k in ("eventos_fx", "pending", "scheduled", "mensajes", "imagenes",
              "hechos", "dia_del_valle"):
        print(f"  {k:>14}  OK={_avg(g_ok, k):6.2f}   FALLO={_avg(g_no, k):6.2f}")
    print(f"  {'GT capado':>14}  OK={sum(1 for c in g_ok if c['gt_capado'])}/6"
          f"        FALLO={sum(1 for c in g_no if c['gt_capado'])}/19")

    (HERE / "resumen.json").write_text(
        json.dumps(resumen, indent=2, default=str), encoding="utf-8")

    exactos = [f.rid for f in fichas if f.delta == 0]
    print(f"dentro de ±1% {len(buenos)}/25 (exactos {len(exactos)}) · fallos {len(malos)}")
    print("cubos:", {k: len(v) for k, v in sorted(resumen["cubos"].items())})
    print(f"(a) safe mal + earliest BIEN: {len(grupo_a)} -> "
          f"{[f.rid for f in grupo_a]}")
    print(f"(b) safe mal + earliest MAL : {len(grupo_b)} -> "
          f"{[f.rid for f in grupo_b]}")
    print("clavados:", [f.rid for f in buenos])
    print()
    for f in malos:
        print(f"{f.rid}  delta {'+' if f.delta > 0 else ''}{m(f.delta):>18} "
              f"({f.delta_pct:6.1f}%)  [{f.bucket}]  {f.razon[:120]}")
    return fichas


def calibrar(n=30, semilla=7):
    """¿El buscador de coincidencias sabe ponerse ROJO?

    Un comprobador que sólo se ha puesto verde no ha demostrado nada. Se le dan
    deltas AL AZAR del mismo orden de magnitud que los reales: si encuentra
    pareja igual de a menudo, el método no distingue señal de ruido.
    """
    import random
    ds = loader.load()
    try:
        from extraction.facts import load_facts
        facts = load_facts(ds)
    except Exception:
        facts = {"images": {}, "messages": {}}
    try:
        from decision.engine import decide as dfn
    except Exception:
        dfn = None
    try:
        from evaluation.verifier import verify as vfn
    except Exception:
        vfn = None
    gt = {r["request_id"]: r for r in loader.load_samples()}
    random.seed(semilla)
    reales = tot = falsos = ntot = 0
    detalle = []
    for req in loader.sample_requests():
        f = analizar(ds, facts, req, gt[req.request_id], dfn, vfn)
        if f.pasa1:
            continue
        cands = candidatos(ds, req, reconstruct(ds, req, facts), facts)
        tot += 1
        h = coincidencias(f.delta, cands, top=99)
        reales += 1 if h else 0
        detalle.append((f.rid, len(h)))
        mag = abs(f.delta)
        for _ in range(n):
            r = D(str(random.uniform(float(mag) * 0.3, float(mag) * 3.0)))
            ntot += 1
            falsos += 1 if coincidencias(r, cands) else 0
    print(f"candidatos por sample: ~{len(cands)}")
    print(f"deltas REALES con >=1 coincidencia a ±1%: {reales}/{tot} "
          f"= {reales/tot*100:.0f}%")
    print(f"deltas ALEATORIOS con >=1 coincidencia a ±1%: {falsos}/{ntot} "
          f"= {falsos/ntot*100:.0f}%")
    print("coincidencias por delta real:", detalle)
    if falsos / ntot >= reales / tot:
        print("\nVEREDICTO: el azar acierta igual o más. El método NO tiene señal.")
    else:
        print("\nVEREDICTO: los deltas reales coinciden por encima del azar.")


if __name__ == "__main__":
    if "--calibrar" in sys.argv:
        calibrar()
    else:
        main()
