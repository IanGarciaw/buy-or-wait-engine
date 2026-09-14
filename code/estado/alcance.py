#!/usr/bin/env python3
"""FRENTE 5 — alcance de los mensajes. ¿Cuánto mueve un hecho SIN objetivo?

Corre los 250 con los hechos reales y con variantes donde se neutraliza una clase de
hecho a la vez, y mide el delta en `amount_safe_today` / `earliest_full_payment`.
Sólo lectura sobre producción; las variantes se construyen copiando el dict de hechos.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

import loader                              # noqa: E402
from finance import view as V              # noqa: E402
from extraction.facts import load_facts    # noqa: E402

ds = loader.load()
facts = load_facts(ds)
MSG_ROWS = {r["message_id"]: r for r in csv.DictReader(open(ROOT / "dataset" / "messages.csv"))}


def variante(pred):
    """Copia de `facts` donde los MessageFact que cumplen pred pasan a kind='none'."""
    out = {}
    for uid, lst in facts["messages"].items():
        out[uid] = [f if not pred(f) else replace(f, kind="none") for f in lst]
    return {"images": facts["images"], "messages": out}


def correr(fx):
    res = {}
    for req in ds.requests:
        v = V.build_view(ds, req, fx)
        res[req.request_id] = (v.amount_safe_today, v.earliest_full_payment)
    return res


base = correr(facts)

CASOS = {
    "sin_objetivo:TODOS": lambda f: f.target_event_id is None and f.kind != "none",
    "sin_objetivo:not_yet_cash": lambda f: f.target_event_id is None and f.kind == "not_yet_cash",
    "sin_objetivo:amount_amendment": lambda f: f.target_event_id is None and f.kind == "amount_amendment",
    "sin_objetivo:income_change": lambda f: f.target_event_id is None and f.kind == "income_change",
    "sin_objetivo:income_ended": lambda f: f.target_event_id is None and f.kind == "income_ended",
    "sin_objetivo:income_date_change": lambda f: f.target_event_id is None and f.kind == "income_date_change",
    "sin_objetivo:new_recurring": lambda f: f.target_event_id is None and f.kind == "new_recurring",
    "CON_objetivo:income_date_change": lambda f: f.target_event_id is not None and f.kind == "income_date_change",
    "CON_objetivo:not_yet_cash": lambda f: f.target_event_id is not None and f.kind == "not_yet_cash",
}

print("caso|requests_afectados|safe_cambia|earliest_cambia|delta_abs_total|ejemplos")
for nombre, pred in CASOS.items():
    alt = correr(variante(pred))
    dif_s = [(k, base[k][0], alt[k][0]) for k in base if base[k][0] != alt[k][0]]
    dif_e = [(k, base[k][1], alt[k][1]) for k in base if base[k][1] != alt[k][1]]
    tot = sum(abs(a - b) for _, a, b in dif_s)
    afect = len({k for k, _, _ in dif_s} | {k for k, _, _ in dif_e})
    ej = "; ".join(f"{k}:{a}->{b}" for k, a, b in dif_s[:3])
    print(f"{nombre}|{afect}|{len(dif_s)}|{len(dif_e)}|{tot}|{ej}")

# ¿a cuántos usuarios evaluados llega cada clase de hecho?
print("\n-- cobertura de hechos sobre los 250 requests --")
req_users = {r.user_id for r in ds.requests}
cnt = Counter()
for uid, lst in facts["messages"].items():
    if uid not in req_users:
        continue
    for f in lst:
        if f.kind == "none":
            continue
        cnt[(f.kind, "con_obj" if f.target_event_id else "SIN_obj")] += 1
for k, v in sorted(cnt.items()):
    print("   ", k, v)

# el único income_date_change CON objetivo
print("\n-- income_date_change CON objetivo --")
for uid, lst in facts["messages"].items():
    for f in lst:
        if f.kind == "income_date_change" and f.target_event_id:
            m = MSG_ROWS[f.message_id]
            print(f"   {f.message_id} u={uid} tgt={f.target_event_id} eff={f.effective_date}")
            print("   texto:", m["message_text"][:220].replace("\n", " "))
