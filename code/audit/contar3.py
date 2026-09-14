import csv, collections
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
D = Path(__file__).resolve().parents[2] / "dataset"
def d(s):
    s=(s or "").strip(); return datetime.strptime(s[:10],"%Y-%m-%d").date() if s else None
def n(s):
    s=(s or "").strip().replace(",","")
    if not s: return None
    try: return Decimal(s)
    except Exception: return None
EV=list(csv.DictReader(open(D/"financial_events.csv")))
PR={r["user_id"]:r for r in csv.DictReader(open(D/"financial_profiles.csv"))}
RQ=list(csv.DictReader(open(D/"requests.csv")))
SM=list(csv.DictReader(open(D/"sample_requests.csv")))
MS=list(csv.DictReader(open(D/"messages.csv")))
RT=list(csv.DictReader(open(D/"exchange_rates.csv")))
byu=collections.defaultdict(list)
for e in EV: byu[e["user_id"]].append(e)
byid={e["event_id"]:e for e in EV}
def day_of(e): return d(e["settlement_date"]) or d(e["event_date"])
SMids={r["request_id"] for r in SM}

print("### FX ###")
fx=[e for e in EV if e["currency"]!=PR[e["user_id"]]["home_currency"]]
print(" eventos en moneda != home:",len(fx),"de",len(EV))
print(" por status/dir:",collections.Counter((e["status"],e["direction"]) for e in fx))
print(" usuarios con algun FX:",len({e['user_id'] for e in fx}))
rates={(r["rate_date"],r["from_currency"],r["to_currency"]):r["rate"] for r in RT}
pairs=collections.Counter((r["from_currency"],r["to_currency"]) for r in RT)
print(" pares en exchange_rates:",dict(pairs))
recip=[(a,b) for (a,b) in pairs if (b,a) in pairs]
print(" pares con ambas direcciones:",recip)
# cobertura: cuantos FX tienen fila exacta en su settlement date y direccion
ok=miss_dir=miss_all=0
for e in fx:
    dy=day_of(e); home=PR[e["user_id"]]["home_currency"]
    k=(dy.isoformat(),e["currency"],home)
    ki=(dy.isoformat(),home,e["currency"])
    if k in rates: ok+=1
    elif ki in rates: miss_dir+=1
    else: miss_all+=1
print(f" FX con fila exacta dir declarada: {ok} | solo dir inversa: {miss_dir} | sin fila esa fecha: {miss_all}")

print("\n### mensajes sobre los 6 'Possible duplicate card charge' ###")
dups=[e for e in EV if e["description"]=="Possible duplicate card charge"]
msg_by_ev=collections.defaultdict(list)
for m in MS:
    if m["related_event_id"].strip(): msg_by_ev[m["related_event_id"].strip()].append(m)
rq_by_user={r["user_id"]:r for r in RQ+SM}
for e in dups:
    r=rq_by_user.get(e["user_id"])
    rid=r["request_id"] if r else None
    tag="SAMPLE" if rid in SMids else "REQUEST"
    inhz = r and d(r["request_date"])<=day_of(e)<=d(r["request_date"])+timedelta(days=90)
    print(f" {e['event_id']} {e['user_id']} monto={e['amount']} {e['status']}/{e['direction']} dia={day_of(e)} -> {rid}({tag}) en_horizonte={inhz}")
    for m in msg_by_ev.get(e["event_id"],[]): print("    MSG:",m["message_text"][:190])
    par=byid[e["linked_event_id"]]
    for m in msg_by_ev.get(par["event_id"],[]): print("    MSG(padre):",m["message_text"][:190])

print("\n### 'stop/reduce' candidatos que decision/series.py deja pasar sin filtro de status ###")
bad=[e for e in EV if e["status"] in ("cancelled","failed") and e["flexibility"] not in ("","fixed") and e["direction"]=="debit"]
print(" eventos cancelled/failed con flexibility != fixed:",len(bad))
fxflex=[e for e in EV if e["currency"]!=PR[e["user_id"]]["home_currency"] and e["flexibility"] not in ("","fixed") and e["direction"]=="debit"]
print(" eventos flexibles en moneda extranjera (ahorro sin convertir):",len(fxflex))

print("\n### settled dentro del horizonte (riesgo de doble conteo) ###")
cnt=0
for r in RQ+SM:
    rd=d(r["request_date"]); end=rd+timedelta(days=90)
    for e in byu[r["user_id"]]:
        if e["status"]=="settled" and day_of(e) and rd<=day_of(e)<=end: cnt+=1
print(" settled con dia >= request_date:",cnt)
print("\n### refunds settled pasados que alimentan series de gasto ###")
refs=[e for e in EV if e["event_type"]=="refund"]
print(" refunds:",len(refs),collections.Counter((e['status'],e['direction']) for e in refs))
print(" categorias de refunds:",collections.Counter(e['category'] for e in refs))
origs=[byid[e["linked_event_id"]] for e in refs if e["linked_event_id"] in byid]
print(" categorias de sus originales (gasto que SI entra a la serie):",collections.Counter(e['category'] for e in origs))
