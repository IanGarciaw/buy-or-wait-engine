"""Laboratorio forense INDEPENDIENTE de finance/.

Reimplementa el forecast con interruptores para poder falsar hipotesis sin depender
de los parametros que A1 esta afinando en vivo. Sólo LEE dataset/ vía loader.

Uso:
    from analysis.lab import Cfg, run_all, curva
"""
from __future__ import annotations
import sys, os, calendar
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path: sys.path.insert(0, CODE)

import loader
from contracts import Event, Request, Profile

D = Decimal
DEAD = ("cancelled", "failed", "unrealized")


# ───────────────────────── configuracion ─────────────────────────

@dataclass
class Cfg:
    horizon: int = 90
    include_day_zero: bool = True
    estimator: str = "mean"          # mean|median|last|p75|max|min
    income_estimator: str = "last"
    lookback: int = 6
    var_mult: str = "1.0"
    min_occ: int = 3
    min_occ_income: int = 2
    interval_tol: str = "0.34"
    interval_agree: str = "0.6"
    month_min: int = 26
    month_max: int = 32
    stale_factor: str = "1.2"
    stale_slack: int = 5
    # interruptores de hipotesis
    max_future_monthly: int | None = None   # tope de ocurrencias futuras mensuales
    max_future_weekly: int | None = None
    drop_first_monthly: bool = False        # no cobrar la 1a ocurrencia futura mensual
    income_terminal: bool = True            # "Final/Previous/Last ..." mata la serie
    use_facts: bool = True
    not_yet_cash_mode: str = "suprimir_variable"   # ignorar|suprimir_variable|suprimir_ingreso
    new_recurring_mode: str = "auto"        # gasto|ingreso|ignorar|auto
    multi_fact: bool = False                # aplicar hechos extra escritos a mano


# ───────────────────────── utilidades ─────────────────────────

def _median(xs):
    s = sorted(xs); n = len(s)
    if not n: return D(0)
    return D(s[n//2]) if n % 2 else (D(s[n//2-1]) + D(s[n//2]))/2

def _p75(xs):
    s = sorted(xs)
    if not s: return D(0)
    i = (len(s)-1)*D("0.75"); lo, hi = int(i), min(int(i)+1, len(s)-1)
    return s[lo] + (s[hi]-s[lo])*(i-lo)

def estimate(a, how, lookback):
    if not a: return D(0)
    xs = a[-lookback:] if lookback else a
    if how == "median": return _median(xs)
    if how == "p75": return _p75(xs)
    if how == "max": return max(xs)
    if how == "min": return min(xs)
    if how == "last": return xs[-1]
    return sum(xs)/D(len(xs))

def _clamp(y, m, d): return date(y, m, min(d, calendar.monthrange(y, m)[1]))

def add_months(d, n, anchor):
    m = d.month - 1 + n
    return _clamp(d.year + m//12, m % 12 + 1, anchor)

INCOME_TERMINAL = ("final ", "previous ", "last ")
INCOME_NOT_REC = ("bonus", "commission", "prize", "arrears", "prorated", "lottery",
                  "one-time", "one time", "proceeds", "promotion", "reimbursement")

def norm(s): return " ".join("".join(c for c in s.lower() if not c.isdigit()).split())


@dataclass(frozen=True)
class Serie:
    key: tuple; direction: str; etype: str; cat: str
    kind: str; period: int; amount: D; last_day: date
    occ: int; sample: Event; varies: bool


def _cadence(days, min_occ, c: Cfg):
    if len(days) < min_occ: return None
    gaps = [(days[i+1]-days[i]).days for i in range(len(days)-1)]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < min_occ-1: return None
    med = _median(gaps)
    if med <= 0: return None
    tol = max(D(1), med*D(c.interval_tol))
    agree = sum(1 for g in gaps if abs(D(g)-med) <= tol)
    if D(agree)/D(len(gaps)) < D(c.interval_agree): return None
    if c.month_min <= med <= c.month_max:
        recent = days[-c.lookback:] if c.lookback else days
        cnt = {}
        for d in recent: cnt[d.day] = cnt.get(d.day, 0)+1
        return ("month", max(cnt.items(), key=lambda kv: (kv[1], -kv[0]))[0])
    return ("days", max(1, int(med.to_integral_value())))


def _build(key, rows, as_of, c: Cfg):
    rows.sort(key=lambda r: (r[1], r[0].event_id))
    direction, etype, cat = key[0], key[1], key[2]
    if direction == "credit" and etype == "income":
        limpio = [r for r in rows if not any(w in r[0].description.lower() for w in INCOME_NOT_REC)]
        if len(limpio) >= c.min_occ_income: rows = limpio
    days = [r[1] for r in rows]
    min_occ = c.min_occ_income if (direction == "credit" and etype == "income") else c.min_occ
    cad = _cadence(days, min_occ, c)
    if cad is None: return None
    kind, per = cad
    if direction == "credit":
        if etype != "income": return None
        d = " " + rows[-1][0].description.lower().strip() + " "
        if c.income_terminal and any(w in d for w in INCOME_TERMINAL): return None
    span = 30 if kind == "month" else per
    if (as_of - days[-1]).days > span*D(c.stale_factor) + c.stale_slack: return None
    amounts = [r[2] for r in rows if r[2] is not None]
    if not amounts: return None
    how = c.income_estimator if (direction == "credit" and etype == "income") else c.estimator
    amt = estimate(amounts, how, c.lookback)
    varies = len({a.quantize(D("0.01")) for a in amounts}) > 1
    if varies and direction == "debit": amt = amt*D(c.var_mult)
    return Serie(key, direction, etype, cat, kind, per, amt, days[-1], len(rows), rows[-1][0], varies)


def detect(history, as_of, c: Cfg):
    groups = {}
    for ev, day, amt in history:
        if ev.direction not in ("debit", "credit"): continue
        groups.setdefault((ev.direction, ev.event_type, ev.category), []).append((ev, day, amt))
    out = []
    for key, rows in groups.items():
        s = _build(key, list(rows), as_of, c)
        if s is not None: out.append(s); continue
        subs = {}
        for r in rows: subs.setdefault(key+(norm(r[0].description),), []).append(r)
        if len(subs) < 2: continue
        for sk, sr in subs.items():
            s = _build(sk, sr, as_of, c)
            if s is not None: out.append(s)
    return out


def occurrences(s: Serie, start, end, c: Cfg):
    days = []
    if s.kind == "month":
        cur = _clamp(s.last_day.year, s.last_day.month, s.period)
        if cur <= s.last_day: cur = add_months(cur, 1, s.period)
        g = 0
        while cur <= end and g < 200:
            if cur >= start: days.append(cur)
            cur = add_months(cur, 1, s.period); g += 1
        if c.drop_first_monthly and days: days = days[1:]
        if c.max_future_monthly is not None: days = days[:c.max_future_monthly]
    else:
        cur = s.last_day + timedelta(days=s.period); g = 0
        while cur <= end and g < 400:
            if cur >= start: days.append(cur)
            cur = cur + timedelta(days=s.period); g += 1
        if c.max_future_weekly is not None: days = days[:c.max_future_weekly]
    return days


@dataclass
class Flow:
    day: date; delta: D; note: str; key: tuple | None = None; eid: str | None = None


def _home(a, e, prof, on, ds):
    return a if e.currency == prof.home_currency else loader.convert(a, e.currency, prof.home_currency, on, ds.rates)


def build(ds, req: Request, facts: dict, c: Cfg):
    prof = ds.profiles[req.user_id]
    events = ds.events_by_user.get(req.user_id, ())
    as_of = req.request_date
    end = as_of + timedelta(days=c.horizon)
    first = as_of if c.include_day_zero else as_of + timedelta(days=1)

    # ── hechos
    rellenos, descartar, enmiendas, retrasos = {}, set(), {}, {}
    ing_monto = ing_desde = ing_fecha = None
    ing_terminado = False
    nuevos_deb, nuevos_cre = [], []
    suprimir = ""
    ids = {e.event_id for e in events}
    if c.use_facts and facts:
        for eid, f in (facts.get("images") or {}).items():
            if eid in ids and f is not None and getattr(f, "amount", None) is not None:
                rellenos[eid] = f.amount
        for f in (facts.get("messages") or {}).get(prof.user_id, []) or []:
            kind = getattr(f, "kind", "none")
            tgt = getattr(f, "target_event_id", None)
            if tgt is not None and tgt not in ids: tgt = None
            amt = getattr(f, "amount", None); cur = getattr(f, "currency", None)
            eff = getattr(f, "effective_date", None)
            if amt is not None and cur and cur != prof.home_currency:
                amt = loader.convert(amt, cur, prof.home_currency, eff or as_of, ds.rates)
            if kind in ("cancellation", "duplicate_notice"):
                if tgt: descartar.add(tgt)
            elif kind == "not_yet_cash":
                if tgt: descartar.add(tgt)
                elif c.not_yet_cash_mode != "ignorar": suprimir = c.not_yet_cash_mode
            elif kind == "delay":
                if tgt and eff: retrasos[tgt] = eff
            elif kind == "amount_amendment":
                if tgt and amt is not None: enmiendas[tgt] = amt
                elif amt is not None: ing_monto, ing_desde = amt, eff
            elif kind == "income_change":
                if amt is not None: ing_monto, ing_desde = amt, eff
                elif eff: ing_fecha = eff
            elif kind == "income_date_change":
                if eff: ing_fecha = eff
            elif kind == "income_ended":
                if tgt: descartar.add(tgt)
                else: ing_terminado = True
            elif kind == "new_recurring" and amt is not None:
                modo = c.new_recurring_mode
                if modo == "auto":
                    modo = "ingreso" if _parece_nomina(amt, events, prof, ds, as_of) else "gasto"
                if modo == "ingreso": nuevos_cre.append((eff or as_of, amt))
                elif modo == "gasto": nuevos_deb.append((eff or as_of, amt))

    resolved = []
    for e in events:
        if e.event_id in descartar: continue
        if e.event_id in rellenos and e.amount is None: e = replace(e, amount=rellenos[e.event_id])
        if e.event_id in enmiendas: e = replace(e, amount=enmiendas[e.event_id])
        if e.event_id in retrasos: e = replace(e, settlement_date=retrasos[e.event_id])
        resolved.append(e)

    history, confirmed, sin_monto = [], [], []
    income_anchor = None
    for e in resolved:
        day = e.settlement_date or e.event_date
        if day is None or e.status in DEAD or e.direction == "non_cash": continue
        amt = e.amount
        if amt is not None:
            amt = _home(abs(amt), e, prof, day, ds)
        if e.status == "settled" and day <= as_of:
            history.append((e, day, amt)); continue
        if amt is None: sin_monto.append((e, day)); continue
        if e.status == "pending" and e.direction == "credit": continue
        when = max(day, first) if e.status == "pending" else day
        if when < first or when > end: continue
        sign = amt if e.direction == "credit" else -amt
        confirmed.append(Flow(when, sign, f"{e.status}:{e.description}",
                              (e.direction, e.event_type, e.category), e.event_id))
        if e.direction == "credit" and e.event_type == "income":
            if income_anchor is None or when < income_anchor[0]: income_anchor = (when, amt)

    hist_cad = list(history)
    for e in resolved:
        if e.status == "scheduled" and e.direction == "credit" and e.event_type == "income" \
                and e.amount is not None:
            day = e.settlement_date or e.event_date
            if day and day <= end: hist_cad.append((e, day, _home(e.amount, e, prof, day, ds)))

    ser = detect(hist_cad, as_of, c)
    by_key = {s.key: s for s in ser}
    for e, day in sin_monto:
        if e.status == "pending" and e.direction == "credit": continue
        when = max(day, first) if e.status == "pending" else day
        if when < first or when > end: continue
        s_ = by_key.get((e.direction, e.event_type, e.category))
        if s_ is None: continue
        sign = s_.amount if e.direction == "credit" else -s_.amount
        confirmed.append(Flow(when, sign, f"{e.status}:{e.description}",
                              (e.direction, e.event_type, e.category), e.event_id))

    projected = []
    for s in ser:
        if s.direction == "credit":
            if ing_terminado: continue
            if suprimir == "suprimir_variable" and s.varies: continue
            if suprimir == "suprimir_ingreso" and income_anchor is None: continue
            base = income_anchor[1] if income_anchor is not None else s.amount
            dias = _dias_ingreso(s, ing_fecha, first, end, c)
        else:
            base = s.amount; dias = occurrences(s, first, end, c)
        if base <= 0: continue
        for d in dias:
            a = base
            if s.direction == "credit" and ing_monto is not None and (ing_desde is None or d >= ing_desde):
                a = ing_monto
            projected.append(Flow(d, a if s.direction == "credit" else -a,
                                  f"recurrente:{s.cat}", s.key, s.sample.event_id))
    projected = _dedupe(projected, confirmed, ser)

    if ing_monto is not None and ing_desde is not None and not any(s.direction == "credit" for s in ser) \
            and not ing_terminado:
        nuevos_cre.append((ing_desde, ing_monto))

    extra = [Flow(d, -a, "nuevo gasto (msg)", ("debit", "expense", "__new__")) for d, a in _mensual(nuevos_deb, first, end)]
    extra += [Flow(d, a, "nuevo ingreso (msg)", ("credit", "income", "__new__")) for d, a in _mensual(nuevos_cre, first, end)]
    flows = confirmed + projected + extra
    flows.sort(key=lambda f: (f.day, f.note))
    return prof, as_of, end, flows, ser


def _parece_nomina(amt, events, prof, ds, as_of):
    """Un 'new_recurring' que vale como una nomina completa es una nomina."""
    ingresos = [abs(e.amount) for e in events
                if e.direction == "credit" and e.event_type == "income" and e.amount is not None]
    if not ingresos: return True
    return amt >= max(ingresos)*D("0.5")


def _dias_ingreso(s, nueva, first, end, c):
    if nueva is None or s.kind != "month": return occurrences(s, first, end, c)
    dias = [d for d in occurrences(s, first, end, c) if d < nueva and (nueva-d).days > 25]
    d, k = nueva, 0
    while d <= end and k < 12:
        if d >= first: dias.append(d)
        k += 1; d = add_months(nueva, k, nueva.day)
    return sorted(set(dias))


def _mensual(sem, first, end):
    out = []
    for cuando, monto in sem:
        d = max(cuando, first); k = 0
        while d <= end and k < 12:
            if d >= first: out.append((d, monto))
            k += 1; d = add_months(cuando, k, cuando.day)
    return out


def _dedupe(projected, confirmed, ser):
    win = {s.key[:3]: (15 if s.kind == "month" else max(1, s.period//2)) for s in ser}
    out = list(projected)
    for cf in confirmed:
        k = cf.key[:3] if cf.key else None
        if k not in win: continue
        cand = [(abs((f.day-cf.day).days), i) for i, f in enumerate(out)
                if f.key and f.key[:3] == k and abs((f.day-cf.day).days) <= win[k]]
        if cand: out.pop(min(cand)[1])
    return out


def curva(ds, req, facts, c: Cfg):
    prof, start, end, flows, ser = build(ds, req, facts, c)
    bal = prof.current_available_balance
    pts = []
    i, day = 0, start
    fl = sorted(flows, key=lambda f: f.day)
    while day <= end:
        while i < len(fl) and fl[i].day <= day:
            bal += fl[i].delta; i += 1
        pts.append((day, bal)); day += timedelta(days=1)
    lo_day, lo = min(pts, key=lambda t: (t[1], t[0]))
    return prof, start, end, fl, ser, pts, lo, lo_day


def safe_de(ds, req, facts, c: Cfg):
    prof, start, end, fl, ser, pts, lo, lo_day = curva(ds, req, facts, c)
    head = max(D(0), lo - prof.minimum_balance_to_keep)
    safe = min(req.requested_amount, head)
    need = prof.minimum_balance_to_keep + req.requested_amount
    sm, run = [D(0)]*len(pts), None
    for i in range(len(pts)-1, -1, -1):
        run = pts[i][1] if run is None else min(run, pts[i][1])
        sm[i] = run
    earliest = next((pts[i][0] for i in range(len(pts)) if sm[i] >= need), None)
    return safe, lo, lo_day, earliest


_DS = None; _FACTS = None
def data():
    global _DS, _FACTS
    if _DS is None:
        _DS = loader.load()
        from extraction.facts import load_facts
        _FACTS = load_facts(_DS)
    return _DS, _FACTS


def run_all(c: Cfg, quiet=True):
    ds, facts = data()
    gt = {r["request_id"]: r for r in loader.load_samples()}
    res = []
    for req in loader.sample_requests():
        prof = ds.profiles[req.user_id]
        safe, lo, lo_day, earliest = safe_de(ds, req, facts, c)
        exp = D(gt[req.request_id]["amount_safe_to_pay"])
        tgt = exp + prof.minimum_balance_to_keep
        e_exp = gt[req.request_id]["earliest_date_for_full_payment"] or ""
        res.append(dict(rid=req.request_id, uid=req.user_id, exp=exp, obt=safe,
                        lo=lo, lo_day=lo_day, tgt=tgt, req=req.requested_amount,
                        earl_exp=e_exp, earl_obt=str(earliest or ""),
                        capped=(exp == req.requested_amount)))
    return res


def hit(r, tol="0.01"):
    e, o = r["exp"], r["obt"]
    return abs(e-o) <= max(D("0.01"), abs(e)*D(tol))


def resumen(res, tol="0.01"):
    n1 = sum(1 for r in res if hit(r, tol))
    ne = sum(1 for r in res if r["earl_exp"] == r["earl_obt"])
    return n1, ne
