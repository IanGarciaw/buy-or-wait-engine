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
byu=collections.defaultdict(list)
for e in EV: byu[e["user_id"]].append(e)
byid={e["event_id"]:e for e in EV}
def day_of(e): return d(e["settlement_date"]) or d(e["event_date"])

print("### A) un usuario por request? ###")
c=collections.Counter(r["user_id"] for r in RQ+SM)
print("users con >1 request:", sum(1 for v in c.values() if v>1), "de", len(c))

print("\n### B) pending: donde caen respecto a request_date ###")
for lbl,reqs in (("25 samples",SM),("250 requests",RQ)):
    pos=collections.Counter(); amt_deb=Decimal(0); amt_cre=Decimal(0)
    rq_deb=set(); rq_cre=set()
    for r in reqs:
        rd=d(r["request_date"]); end=rd+timedelta(days=90)
        for e in byu[r["user_id"]]:
            if e["status"]!="pending": continue
            dy=day_of(e)
            rel = "antes" if dy<rd else ("dia0" if dy==rd else ("dentro" if dy<=end else "fuera"))
            pos[(e["direction"],rel)]+=1
            a=n(e["amount"]) or Decimal(0)
            if rd<=dy<=end:
                if e["direction"]=="debit": amt_deb+=a; rq_deb.add(r["request_id"])
                else: amt_cre+=a; rq_cre.add(r["request_id"])
    print(f" {lbl}: {dict(pos)}")
    print(f"   pending DEBIT en horizonte: {len(rq_deb)} requests, suma bruta {amt_deb}")
    print(f"   pending CREDIT en horizonte: {len(rq_cre)} requests, suma bruta {amt_cre}")

print("\n### C) saldo vs historia settled (doble conteo?) ###")
for r in SM[:6]:
    u=r["user_id"]; rd=d(r["request_date"]); p=PR[u]
    s=Decimal(0)
    for e in byu[u]:
        if e["status"]=="settled" and day_of(e) and day_of(e)<=rd and e["currency"]==p["home_currency"]:
            a=n(e["amount"])
            if a is not None: s += a if e["direction"]=="credit" else -a
    print(f"  {r['request_id']} {u} bal={p['current_available_balance']} sum(settled<=rd)={s} min={p['minimum_balance_to_keep']} safe_gt={r['amount_safe_to_pay']}")

print("\n### D) linked_event_id: 58 filas, que representan ###")
L=[e for e in EV if e["linked_event_id"].strip()]
print(" total:",len(L))
cls=collections.Counter()
rows=[]
for e in L:
    par=byid.get(e["linked_event_id"].strip())
    if par is None:
        cls["padre_inexistente"]+=1; rows.append((e["event_id"],"NO-PADRE",e["status"],e["direction"],e["event_type"],e["description"][:50])); continue
    k=(par["status"]+"->"+e["status"], par["event_type"]+"->"+e["event_type"], par["direction"]+"->"+e["direction"])
    cls[k]+=1
    rows.append((e["event_id"],e["linked_event_id"],k,e["description"][:60],par["description"][:40],
                 e["amount"],par["amount"],e["event_date"],par["event_date"]))
for k,v in cls.most_common(): print("  ",v,"x",k)
print("\n  detalle:")
for r in rows: print("   ",r)
