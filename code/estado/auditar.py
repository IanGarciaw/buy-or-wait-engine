#!/usr/bin/env python3
"""AGENTE C — auditoría de reconstrucción de estado. Sólo lectura sobre producción.

Corre el reconstructor real (finance.view.reconstruct) sobre los 250 requests con los
hechos de caché y observa qué pasa con cada evento. No modifica nada.

    python3 code/estado/auditar.py > code/estado/datos.txt
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

import loader                     # noqa: E402
from finance import view as V     # noqa: E402
from extraction.facts import load_facts  # noqa: E402

ds = loader.load()
facts = load_facts(ds)
RAW = {r["event_id"]: r for r in csv.DictReader(open(ROOT / "dataset" / "financial_events.csv"))}
REQ_BY_USER = defaultdict(list)
for r in ds.requests:
    REQ_BY_USER[r.user_id].append(r)

# ── corrida real: un reconstruct por request, se anota el destino de cada evento ──
seen_flow: dict[str, set] = defaultdict(set)      # event_id -> {(request_id, tipo)}
seen_hist: set = set()
per_request = {}
for req in ds.requests:
    rec = V.reconstruct(ds, req, facts)
    ids = set()
    for f in rec.flows:
        if f.event_id is None:
            continue
        tipo = "proyectado" if f.note.startswith("recurrente") else "confirmado"
        seen_flow[f.event_id].add((req.request_id, tipo, str(f.day), str(f.delta)))
        ids.add(f.event_id)
    per_request[req.request_id] = (rec, ids)

print("### CORRIDA ###")
print("requests:", len(ds.requests))

# ─────────────────────────────────────────────────────────────────────────────
print("\n### F2 · LINKED ###")
linked = [r for r in RAW.values() if r["linked_event_id"].strip()]


def clase(hijo, padre):
    d = hijo["description"].lower()
    if "authorization" in (padre or {}).get("description", "").lower():
        return "autorizacion->cobro"
    if "reversal" in d:
        return "cargo->reverso (original+reverso liquidados)"
    if "reimbursement" in d:
        return "gasto->reembolso del empleador"
    if "pending merchant refund" in d:
        return "compra->reembolso PENDIENTE"
    if "valuation" in d:
        return "compra->valuacion (non_cash/unrealized)"
    if "sale proceeds" in d:
        return "compra->venta (proceeds)"
    if "retry" in d:
        return "fallido->reintento"
    if "duplicate" in d:
        return "original->DUPLICADO pendiente"
    return "??? " + d[:40]


clases = defaultdict(list)
for h in linked:
    p = RAW.get(h["linked_event_id"].strip())
    clases[clase(h, p)].append((h, p))

for k, v in sorted(clases.items(), key=lambda kv: -len(kv[1])):
    print(f"\n-- {k}  (n={len(v)})")
    hijo_en, padre_en = Counter(), Counter()
    for h, p in v:
        he = [t for t in seen_flow.get(h["event_id"], ()) if t[1] == "confirmado"]
        pe = [t for t in seen_flow.get(p["event_id"], ()) if t[1] == "confirmado"] if p else []
        hijo_en["flujo" if he else "no"] += 1
        padre_en["flujo" if pe else "no"] += 1
    print("   hijo  en curva:", dict(hijo_en), " padre en curva:", dict(padre_en))
    h, p = v[0]
    print(f"   ejemplo: {h['event_id']} {h['status']}/{h['direction']}/{h['event_type']} "
          f"<- {p['event_id']} {p['status']}/{p['direction']}/{p['event_type']}")
    usuarios = {h["user_id"] for h, _ in v}
    con_req = {u for u in usuarios if u in REQ_BY_USER}
    print(f"   usuarios: {len(usuarios)} · con request evaluado: {len(con_req)}")

# detalle por fila para la tabla
print("\n-- DETALLE 58 --")
for h in linked:
    p = RAW.get(h["linked_event_id"].strip())
    he = sorted(t for t in seen_flow.get(h["event_id"], ()) if t[1] == "confirmado")
    pe = sorted(t for t in seen_flow.get(p["event_id"], ()) if t[1] == "confirmado") if p else []
    evaluado = h["user_id"] in REQ_BY_USER
    print(f"{h['event_id']}|{clase(h,p)}|{h['status']}|{h['direction']}|{h['amount']}|"
          f"{p['event_id']}|{p['status']}|{p['direction']}|{p['amount']}|"
          f"eval={evaluado}|hijo_flujo={len(he)}|padre_flujo={len(pe)}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n### F3 · PENDING ###")
pend = [r for r in RAW.values() if r["status"] == "pending"]
print("pending totales:", len(pend), Counter(r["direction"] for r in pend))
pd_ = [r for r in pend if r["direction"] == "debit"]
pc_ = [r for r in pend if r["direction"] == "credit"]
for nombre, grupo in (("DEBITO", pd_), ("CREDITO", pc_)):
    evaluados = [r for r in grupo if r["user_id"] in REQ_BY_USER]
    en_curva = [r for r in evaluados if any(t[1] == "confirmado" for t in seen_flow.get(r["event_id"], ()))]
    print(f"  pending {nombre}: total={len(grupo)} de usuarios evaluados={len(evaluados)} "
          f"en curva={len(en_curva)}")
    fuera = [r for r in evaluados if r not in en_curva]
    print(f"    fuera de curva: {[r['event_id'] for r in fuera]}")
    print("    tipos:", Counter(r["event_type"] for r in grupo))
    print("    descripciones:", Counter(r["description"] for r in grupo).most_common(6))

# ¿algún pending debit se reservó FUERA de su fecha (movido a hoy)?
movidos = []
for r in pd_:
    if r["user_id"] not in REQ_BY_USER:
        continue
    for (rid, tipo, day, delta) in seen_flow.get(r["event_id"], ()):
        if tipo != "confirmado":
            continue
        real = (r["settlement_date"] or r["event_date"]).strip()
        if day != real:
            movidos.append((r["event_id"], rid, real, day))
print("  pending debit reservados en fecha != settlement:", len(movidos), movidos[:12])

# horizonte: pending debit de usuarios evaluados cuya fecha cae fuera de los 90 días
print("\n### F3b · otros estados ###")
print("scheduled:", Counter(r["direction"] for r in RAW.values() if r["status"] == "scheduled"))
sched_futuro_credit = [r for r in RAW.values() if r["status"] == "scheduled" and r["direction"] == "credit"]
print("scheduled credit:", len(sched_futuro_credit),
      Counter(r["event_type"] for r in sched_futuro_credit))

# ─────────────────────────────────────────────────────────────────────────────
print("\n### F4 · IMAGENES ###")
img_cache = json.load(open(CODE / "cache" / "images.json"))
img_rows = {r["image_id"]: r for r in csv.DictReader(open(ROOT / "dataset" / "images.csv"))}
blank = [r for r in RAW.values() if not r["amount"].strip()]
print("eventos con amount en blanco:", len(blank))
prof_home = {p.user_id: p.home_currency for p in ds.profiles.values()}
for iid in sorted(img_cache):
    f = img_cache[iid]
    e = RAW.get(f["event_id"])
    ir = img_rows.get(iid, {})
    evaluado = e["user_id"] in REQ_BY_USER if e else False
    usado = any(t[1] == "confirmado" for t in seen_flow.get(f["event_id"], ())) if e else False
    hist = e and e["status"] == "settled"
    print(f"{iid}|{f['event_id']}|{f['amount']}|{f['label']}|{f['confidence']}|"
          f"{e['currency'] if e else '?'}|home={prof_home.get(e['user_id']) if e else '?'}|"
          f"{e['event_type'] if e else '?'}|{e['direction'] if e else '?'}|{e['status'] if e else '?'}|"
          f"{e['description'][:44] if e else '?'}|{e['event_date'] if e else '?'}|"
          f"csv_rel={ir.get('related_event_id')}|eval={evaluado}|en_curva={usado}")
huerfanas = [iid for iid in img_cache if img_cache[iid]["event_id"] not in RAW]
print("imagenes cuyo event_id no existe:", huerfanas)
sin_img = [r["event_id"] for r in blank if r["event_id"] not in {f["event_id"] for f in img_cache.values()}]
print("eventos en blanco SIN imagen resuelta:", sin_img)

# ─────────────────────────────────────────────────────────────────────────────
print("\n### F5 · MENSAJES ###")
msg_cache = json.load(open(CODE / "cache" / "messages.json"))
msg_rows = {r["message_id"]: r for r in csv.DictReader(open(ROOT / "dataset" / "messages.csv"))}
print("mensajes csv:", len(msg_rows), "hechos en cache:", len(msg_cache))
print("csv con related_event_id:", sum(1 for r in msg_rows.values() if r["related_event_id"].strip()))
print("csv con request_id:", sum(1 for r in msg_rows.values() if r["request_id"].strip()))
kinds = Counter(f["kind"] for f in msg_cache.values())
print("kinds:", dict(kinds))
con_obj = Counter()
sin_obj = Counter()
for mid, f in msg_cache.items():
    (con_obj if f["target_event_id"] else sin_obj)[f["kind"]] += 1
print("CON objetivo:", dict(con_obj), "=", sum(con_obj.values()))
print("SIN objetivo:", dict(sin_obj), "=", sum(sin_obj.values()))
# ¿cuántos de los que NO tienen target sí tenían related_event_id en el CSV?
perdidos = [mid for mid, f in msg_cache.items()
            if not f["target_event_id"] and msg_rows.get(mid, {}).get("related_event_id", "").strip()
            and f["kind"] != "none"]
print("hecho accionable SIN target pero el CSV sí daba related_event_id:", len(perdidos), perdidos)
# ¿hechos accionables sin target NI related_event_id? -> ámbito global
globales = [(mid, f["kind"]) for mid, f in msg_cache.items()
            if not f["target_event_id"] and f["kind"] != "none"
            and not msg_rows.get(mid, {}).get("related_event_id", "").strip()]
print("hechos accionables de ambito GLOBAL (sin related en csv):", len(globales))
print("  por kind:", Counter(k for _, k in globales))
# usuarios con >1 mensaje
por_user = Counter(r["user_id"] for r in msg_rows.values())
print("usuarios con >1 mensaje:", sum(1 for v in por_user.values() if v > 1),
      "max:", por_user.most_common(3))
# mensajes ligados a request pero usados para todo el usuario
usados_por_request = [mid for mid, r in msg_rows.items()
                      if r["request_id"].strip() and msg_cache.get(mid, {}).get("kind") != "none"]
print("mensajes con request_id y hecho accionable:", len(usados_por_request))
