#!/usr/bin/env python3
"""Contrafactual del ciclo de vida: ¿cuánto pesa NO leer `linked_event_id`?

Para cada par enlazado cuyo usuario SÍ tiene request evaluado, se reconstruye con el
dataset real y con un dataset copia donde el evento original (el padre) se elimina.
La diferencia en `amount_safe_today` mide el efecto de tratar un cargo revertido /
reembolsado como si fuera gasto recurrente normal.

NO modifica producción ni el dataset en disco: la copia es en memoria.
"""
from __future__ import annotations

import csv
import sys
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
RAW = {r["event_id"]: r for r in csv.DictReader(open(ROOT / "dataset" / "financial_events.csv"))}
req_by_user = {r.user_id: r for r in ds.requests}


def sin(ds, user_id, drop: set):
    evs = tuple(e for e in ds.events_by_user[user_id] if e.event_id not in drop)
    return replace(ds, events_by_user={**ds.events_by_user, user_id: evs})


def safe(d, req):
    v = V.build_view(d, req, facts)
    return v.amount_safe_today, v.earliest_full_payment


linked = [r for r in RAW.values() if r["linked_event_id"].strip()]
print("clase|hijo|padre|user|request|safe_real|safe_sin_padre|delta|earliest_real|earliest_sin")
tot = 0
for h in linked:
    p = RAW.get(h["linked_event_id"].strip())
    if not p:
        continue
    u = h["user_id"]
    req = req_by_user.get(u)
    if req is None:
        continue
    d = h["description"].lower()
    if "reversal" in d:
        clase, drop = "cargo->reverso", {p["event_id"], h["event_id"]}
    elif "reimbursement" in d:
        clase, drop = "gasto->reembolso", {p["event_id"]}
    elif "duplicate" in d:
        clase, drop = "duplicado_pendiente", {h["event_id"]}
    elif "sale proceeds" in d:
        clase, drop = "venta_inversion", {h["event_id"]}
    else:
        continue
    a, ea = safe(ds, req)
    b, eb = safe(sin(ds, u, drop), req)
    delta = b - a
    if delta != 0:
        tot += 1
    print(f"{clase}|{h['event_id']}|{p['event_id']}|{u}|{req.request_id}|{a}|{b}|{delta}|{ea}|{eb}")
print(f"\npares donde quitar el enlazado CAMBIA safe: {tot}")
