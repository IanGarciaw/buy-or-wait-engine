"""Conteos de auditoria. Solo lectura sobre dataset/. No toca nada fuera de code/audit/."""
import csv, collections, sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

D = Path(__file__).resolve().parents[2] / "dataset"

def d(s):
    s=(s or "").strip()
    return datetime.strptime(s[:10],"%Y-%m-%d").date() if s else None
def n(s):
    s=(s or "").strip().replace(",","")
    if not s: return None
    try: return Decimal(s)
    except Exception: return None

EV=list(csv.DictReader(open(D/"financial_events.csv")))
PR={r["user_id"]:r for r in csv.DictReader(open(D/"financial_profiles.csv"))}
RQ=list(csv.DictReader(open(D/"requests.csv")))
SM=list(csv.DictReader(open(D/"sample_requests.csv")))
by_user=collections.defaultdict(list)
for e in EV: by_user[e["user_id"]].append(e)

def day_of(e): return d(e["settlement_date"]) or d(e["event_date"])

def scope(reqs, label):
    H=90
    tot=collections.Counter(); reqs_hit=collections.Counter()
    boundary=collections.Counter(); fx=collections.Counter()
    users=set()
    for r in reqs:
        u=r["user_id"]; users.add(u)
        rd=d(r["request_date"]); end=rd+timedelta(days=H)
        home=PR[u]["home_currency"]
        hits=collections.Counter()
        for e in by_user[u]:
            dy=day_of(e)
            if dy is None: continue
            st,di=e["status"],e["direction"]
            # contadores por celda dentro del horizonte
            if rd<=dy<=end:
                hits[(st,di)]+=1
                if e["currency"]!=home: hits[("FX",st+"/"+di)]+=1
                if dy==rd: boundary[("dia_0",st,di)]+=1
                if dy==end: boundary[("dia_90",st,di)]+=1
            elif dy<rd:
                hits[("PASADO",st,di)]+=1
        for k,v in hits.items():
            tot[k]+=v
            reqs_hit[k]+=1
    print(f"\n===== {label}: {len(reqs)} requests, {len(users)} usuarios =====")
    print("--- eventos DENTRO de [request_date, +90] por (status,direction) ---")
    for k in sorted(tot, key=str):
        if isinstance(k[0],str) and k[0] in ("PASADO","FX"): continue
        print(f"  {k}: {tot[k]} eventos en {reqs_hit[k]} requests")
    print("--- FX (moneda != home) dentro del horizonte ---")
    for k in sorted(tot,key=str):
        if k[0]=="FX": print(f"  {k[1]}: {tot[k]} eventos en {reqs_hit[k]} requests")
    print("--- eventos ANTERIORES a request_date (historia) ---")
    for k in sorted(tot,key=str):
        if k[0]=="PASADO": print(f"  {k[1:]}: {tot[k]} eventos en {reqs_hit[k]} requests")
    print("--- FRONTERAS ---")
    for k in sorted(boundary,key=str): print(f"  {k}: {boundary[k]}")

scope(SM,"25 SAMPLES")
scope(RQ,"250 REQUESTS")
