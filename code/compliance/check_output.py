"""Read-only compliance validator for output.csv. Stdlib only."""
import csv, re, sys, datetime
from pathlib import Path

ROOT = Path("/Users/gamagarcia/Desktop/BUY-OR-WAIT")

REQ_COLS = ["request_id","amount_safe_to_pay","affordability_status",
            "recommended_payment_method","payment_plan",
            "earliest_date_for_full_payment","spending_changes_needed",
            "decision_explanation"]

STATUS = {"affordable_now","affordable_with_plan","affordable_later","not_affordable"}
METHOD = {"full_payment","partial_payment","installments","wait","not_recommended"}

def rd(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

out_raw = list(csv.reader(open(ROOT/"output.csv", newline="", encoding="utf-8-sig")))
hdr = out_raw[0]
out = rd(ROOT/"output.csv")
req = rd(ROOT/"dataset/requests.csv")

fails = []
info = []

# 1. columns + order
info.append(f"header == required order: {hdr == REQ_COLS}  (got {len(hdr)} cols)")
if hdr != REQ_COLS: fails.append(f"COLUMN ORDER/NAMES: got {hdr}")

# 2. row count
info.append(f"output data rows={len(out)}  requests.csv rows={len(req)}")
if len(out) != len(req): fails.append(f"ROW COUNT {len(out)} != {len(req)}")

# 3. id coverage
oid = [r["request_id"] for r in out]
rid = [r["request_id"] for r in req]
dups = [i for i in set(oid) if oid.count(i) > 1]
missing = [i for i in rid if i not in set(oid)]
extra = [i for i in oid if i not in set(rid)]
info.append(f"duplicates={len(dups)} missing={len(missing)} extra={len(extra)}")
info.append(f"order identical to requests.csv: {oid == rid}")
if dups: fails.append(f"DUPLICATE request_id: {dups[:10]}")
if missing: fails.append(f"MISSING request_id: {missing[:10]}")
if extra: fails.append(f"EXTRA request_id: {extra[:10]}")

by_req = {r["request_id"]: r for r in req}

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
def isdate(s):
    if not DATE.match(s): return False
    try: datetime.date.fromisoformat(s); return True
    except ValueError: return False

bad_bounds=[]; bad_status=[]; bad_method=[]; bad_plan=[]; bad_date=[]; bad_chg=[]
bad_empty_expl=[]; nonchrono=[]; partial_viol=[]; now_viol=[]
amt_fmt=[]

PLAN_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2}):(-?\d+(?:\.\d+)?)$")
CHG = re.compile(r"^(stop:[A-Za-z0-9_\-]+|reduce_to:[A-Za-z0-9_\-]+:-?\d+(?:\.\d+)?)$")

for r in out:
    i = r["request_id"]
    src = by_req.get(i)
    # amount bounds
    try:
        a = float(r["amount_safe_to_pay"])
    except ValueError:
        amt_fmt.append((i, r["amount_safe_to_pay"])); a=None
    if a is not None and src is not None:
        ra = float(src["requested_amount"])
        if not (0 <= a <= ra + 1e-9):
            bad_bounds.append((i, a, ra))
    if r["affordability_status"] not in STATUS: bad_status.append((i, r["affordability_status"]))
    if r["recommended_payment_method"] not in METHOD: bad_method.append((i, r["recommended_payment_method"]))
    # payment plan
    pp = r["payment_plan"]
    if pp != "none":
        parts = pp.split("|")
        ds=[]
        for p in parts:
            m = PLAN_ENTRY.match(p)
            if not m or not isdate(m.group(1)):
                bad_plan.append((i,p)); break
            ds.append(m.group(1))
        else:
            if ds != sorted(ds): nonchrono.append((i,pp))
    # earliest date
    ed = r["earliest_date_for_full_payment"]
    if ed != "" and not isdate(ed): bad_date.append((i,ed))
    # spending changes
    sc = r["spending_changes_needed"]
    if sc != "none":
        items = sc.split("|")
        if len(items) > 3: bad_chg.append((i,"more than 3",sc))
        for it in items:
            if not CHG.match(it): bad_chg.append((i,"malformed",it))
        ev=[it.split(":")[1] for it in items if CHG.match(it)]
        if len(ev)!=len(set(ev)): bad_chg.append((i,"stop+reduce same event",sc))
    if not r["decision_explanation"].strip(): bad_empty_expl.append(i)
    # spec: affordable_now => earliest == request_date
    if src and r["affordability_status"]=="affordable_now" and ed != src["request_date"]:
        now_viol.append((i, ed, src["request_date"]))
    # spec: partial_payment constraints
    if r["recommended_payment_method"]=="partial_payment" and src:
        ra=float(src["requested_amount"])
        prob=[]
        if r["affordability_status"]!="affordable_with_plan": prob.append("status!=affordable_with_plan")
        if not (0 < a < ra): prob.append(f"not 0<{a}<{ra}")
        parts = pp.split("|") if pp!="none" else []
        if len(parts)!=2: prob.append(f"{len(parts)} payments not 2")
        else:
            try:
                d1,v1=parts[0].split(":"); d2,v2=parts[1].split(":")
                if d1!=src["request_date"]: prob.append(f"p1 date {d1}!=request_date {src['request_date']}")
                if abs(float(v1)-a)>0.01: prob.append(f"p1 amt {v1}!=asp {a}")
                if abs(float(v1)+float(v2)-ra)>0.01: prob.append(f"sum {float(v1)+float(v2)}!=requested {ra}")
                if ed and d2!=ed: prob.append(f"p2 date {d2}!=earliest {ed}")
                if ed and ed > src["desired_completion_date"]: prob.append(f"earliest {ed} > deadline {src['desired_completion_date']}")
            except Exception as e: prob.append(f"parse {e}")
        if prob: partial_viol.append((i,prob))

def rep(name, lst, cap=8):
    info.append(f"{name}: {len(lst)}" + (f"  e.g. {lst[:cap]}" if lst else ""))
    return lst

rep("amount not numeric", amt_fmt)
rep("bounds violations 0<=asp<=requested", bad_bounds)
rep("status out of domain", bad_status)
rep("method out of domain", bad_method)
rep("payment_plan malformed", bad_plan)
rep("payment_plan not chronological", nonchrono)
rep("earliest_date bad format", bad_date)
rep("spending_changes malformed/>3/dup-event", bad_chg)
rep("empty decision_explanation", bad_empty_expl)
rep("affordable_now with earliest != request_date", now_viol)
rep("partial_payment rule violations", partial_viol)

for l in (amt_fmt,bad_bounds,bad_status,bad_method,bad_plan,nonchrono,bad_date,bad_chg,bad_empty_expl,now_viol,partial_viol):
    pass

# distributions
from collections import Counter
info.append("status dist: "+str(Counter(r["affordability_status"] for r in out)))
info.append("method dist: "+str(Counter(r["recommended_payment_method"] for r in out)))
info.append("plan==none: "+str(sum(1 for r in out if r["payment_plan"]=="none")))
info.append("earliest empty: "+str(sum(1 for r in out if r["earliest_date_for_full_payment"]=="")))
info.append("changes==none: "+str(sum(1 for r in out if r["spending_changes_needed"]=="none")))

hard = bad_bounds+bad_status+bad_method+bad_plan+bad_date+amt_fmt
print("\n".join(info))
print("\nHARD SCHEMA FAILURES:", len(hard))
print("CONTRACT FAILURES (structural):", fails)
