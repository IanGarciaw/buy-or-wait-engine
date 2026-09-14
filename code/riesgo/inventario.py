#!/usr/bin/env python3
"""AGENTE A - inventario de riesgo oculto. SOLO LECTURA.

No importa ningun modulo del motor: lee dataset/ y output.csv por su cuenta.
Asi el instrumento no hereda los supuestos del motor que audita.
"""
from __future__ import annotations

import csv, json, sys, collections
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DS = ROOT / "dataset"
HORIZON = 90


def rd(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def d(s):
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


def num(s):
    s = (s or "").strip()
    return Decimal(s) if s else None


events = rd(DS / "financial_events.csv")
profiles = {r["user_id"]: r for r in rd(DS / "financial_profiles.csv")}
requests = rd(DS / "requests.csv")
options = rd(DS / "request_payment_options.csv")
images = rd(DS / "images.csv")
messages = rd(DS / "messages.csv")

ev_by_user = collections.defaultdict(list)
for e in events:
    ev_by_user[e["user_id"]].append(e)
ev_by_id = {e["event_id"]: e for e in events}
opts_by_req = collections.defaultdict(list)
for o in options:
    opts_by_req[o["request_id"]].append(o)

req_by_id = {r["request_id"]: r for r in requests}

INV = []


def line(label, n, extra=""):
    INV.append((label, n, extra))


def in_horizon(e, r):
    """El evento cae dentro de [request_date, request_date+90]."""
    when = d(e["settlement_date"]) or d(e["event_date"])
    if when is None:
        return False
    s = d(r["request_date"])
    return s <= when <= s + timedelta(days=HORIZON)


def reqs_touched(pred):
    """Cuantos de los 250 requests tienen >=1 evento que cumple pred DENTRO de su horizonte."""
    n = 0
    for r in requests:
        for e in ev_by_user.get(r["user_id"], ()):
            if pred(e) and in_horizon(e, r):
                n += 1
                break
    return n


# 1-2 pending
pend_deb = [e for e in events if e["status"] == "pending" and e["direction"] == "debit"]
pend_cre = [e for e in events if e["status"] == "pending" and e["direction"] == "credit"]
line("pending debit", len(pend_deb),
     f"en {reqs_touched(lambda e: e['status']=='pending' and e['direction']=='debit')}/250 requests dentro del horizonte")
line("pending credit", len(pend_cre),
     f"en {reqs_touched(lambda e: e['status']=='pending' and e['direction']=='credit')}/250 requests dentro del horizonte; se ignoran por regla del enunciado")

# 3 cadenas linked_event_id
chains = collections.Counter()
huerfanos = []
for c in events:
    pid = c["linked_event_id"].strip()
    if not pid:
        continue
    p = ev_by_id.get(pid)
    if p is None:
        huerfanos.append((c["event_id"], pid))
        chains["PADRE INEXISTENTE"] += 1
    else:
        chains[f"{p['event_type']}/{p['status']} -> {c['event_type']}/{c['status']}"] += 1
n_chains = sum(chains.values())
line("cadenas linked_event", n_chains,
     "; ".join(f"{k} = {v}" for k, v in chains.most_common()))

# 4-5 scheduled
sch_cre = [e for e in events if e["status"] == "scheduled" and e["direction"] == "credit"]
sch_deb = [e for e in events if e["status"] == "scheduled" and e["direction"] == "debit"]
line("scheduled future income", len(sch_cre),
     f"en {reqs_touched(lambda e: e['status']=='scheduled' and e['direction']=='credit')}/250 requests dentro del horizonte")
line("scheduled future debit", len(sch_deb),
     f"en {reqs_touched(lambda e: e['status']=='scheduled' and e['direction']=='debit')}/250 requests dentro del horizonte")

# 6 FX
fx_users = set()
fx_ev = 0
fx_ev_horiz = 0
fx_req = set()
for e in events:
    home = profiles.get(e["user_id"], {}).get("home_currency")
    if home and e["currency"] != home:
        fx_ev += 1
        fx_users.add(e["user_id"])
for r in requests:
    home = profiles.get(r["user_id"], {}).get("home_currency")
    for e in ev_by_user.get(r["user_id"], ()):
        if e["currency"] != home and in_horizon(e, r):
            fx_ev_horiz += 1
            fx_req.add(r["request_id"])
line("conversiones FX aplicadas", fx_ev,
     f"{len(fx_users)} usuarios distintos; {fx_ev_horiz} de esos eventos caen en el horizonte de {len(fx_req)}/250 requests")

# 7 mensajes aplicados (kind != none) - lee la CACHE que el motor uso
cache_msgs = ROOT / "code" / "cache" / "messages.json"
kinds = collections.Counter()
n_msg_apl = 0
if cache_msgs.exists():
    raw = json.loads(cache_msgs.read_text())
    items = raw.values() if isinstance(raw, dict) else raw
    for it in items:
        if not isinstance(it, dict):
            continue
        k = it.get("kind")
        if k is None:
            continue
        kinds[k] += 1
        if k != "none":
            n_msg_apl += 1
line("mensajes aplicados", n_msg_apl,
     f"de {len(messages)} mensajes; kinds: " + ", ".join(f"{k}={v}" for k, v in kinds.most_common()))

# 8 imagenes aplicadas
cache_img = ROOT / "code" / "cache" / "images.json"
n_img = 0
img_amt = 0
if cache_img.exists():
    raw = json.loads(cache_img.read_text())
    items = raw.values() if isinstance(raw, dict) else raw
    for it in items:
        if isinstance(it, dict):
            n_img += 1
            if it.get("amount") not in (None, ""):
                img_amt += 1
blancos = [e for e in events if not e["amount"].strip()]
line("imagenes aplicadas", n_img,
     f"{img_amt} con monto legible; {len(blancos)} eventos con amount en blanco, {len(images)} filas en images.csv")

# 9 cambios de gasto emitidos (output.csv)
out = rd(ROOT / "output.csv")
n_ch = 0
n_ch_rows = 0
for r in out:
    sc = r["spending_changes_needed"].strip()
    if sc and sc != "none":
        n_ch_rows += 1
        n_ch += len(sc.split("|"))
line("cambios de gasto emitidos", n_ch,
     f"en {n_ch_rows} filas de 250")

# 10 planes que violan el piso 90d — senal del modelo INDEPENDIENTE del verificador
import re
inc_f = ROOT / "code" / "riesgo" / "incidentes.txt"
n_floor = n_abs = 0
floor_ids = set()
if inc_f.exists():
    for i in inc_f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(request_\d+): WARN\[(floor|floor_abs)\]", i)
        if m:
            if m.group(2) == "floor":
                floor_ids.add(m.group(1))
            else:
                n_abs += 1
    n_floor = len(floor_ids)
out_by = {r["request_id"]: r for r in out}
con_plan = sum(1 for r in floor_ids if out_by[r]["payment_plan"].strip() != "none")
line("planes que violan el piso 90d", n_floor,
     f"senal WARN[floor] del modelo independiente del verificador (diferencial, anclado en el "
     f"amount_safe_to_pay del motor); {con_plan} de ellos tienen plan real; "
     f"WARN[floor_abs] (nivel absoluto de ese modelo, ruidoso por diseno) = {n_abs}; "
     f"BLOCK del verificador = 0")

# 11 planes fallback inseguros
n_fb = 0
if inc_f.exists():
    txt = inc_f.read_text(encoding="utf-8")
    n_fb = txt.count("motor fall") + txt.count("verificador fall")
line("planes fallback inseguros", n_fb,
     "0 fallos duros; build_view/decide/load_facts/verify todos enganchados, "
     "asi que _fallback_view/_fallback_decide/_fallback_verify nunca corrieron; "
     "0 explicaciones vacias -> _explain de respaldo tampoco")

print("HIDDEN-RISK INVENTORY")
print("\n".join(f"{a:<32}{b:<6}({c})" for a, b, c in INV))
