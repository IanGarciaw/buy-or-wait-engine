#!/usr/bin/env python3
"""Calibracion del auditor: cada mutacion DEBE ponerlo rojo en su clase.

Sin esto, 'ANOMALIAS: 0' no dice nada. Trabaja sobre COPIAS en un temporal;
output.csv nunca se toca.
"""
import csv, subprocess, sys, tempfile, shutil, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "output.csv"
AUD = ROOT / "code" / "riesgo" / "auditar_salida.py"
COLS = ["request_id","amount_safe_to_pay","affordability_status",
        "recommended_payment_method","payment_plan",
        "earliest_date_for_full_payment","spending_changes_needed",
        "decision_explanation"]

base = list(csv.DictReader(open(SRC, newline="", encoding="utf-8")))
idx = {r["request_id"]: i for i, r in enumerate(base)}


def write(rows, path, header=COLS):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def run(path):
    out = subprocess.run([sys.executable, str(AUD), str(path)],
                         capture_output=True, text=True, cwd=str(ROOT))
    return out.stdout


def mut(name, tag, fn, header=COLS):
    rows = [dict(r) for r in base]
    rows = fn(rows) or rows
    p = Path(tmp) / f"{name}.csv"
    write(rows, p, header)
    o = run(p)
    hit = f"[{tag}]" in o
    print(f"  {'ROJO ' if hit else 'VERDE'}  {name:<26} espera [{tag}]  -> {'OK' if hit else 'FALLO: el instrumento NO detecta esta clase'}")
    if not hit:
        print("      salida:", o.strip().splitlines()[-1])
    return hit


# fila de referencia con cada forma
def find(pred):
    return next(r["request_id"] for r in base if pred(r))

R_FULL = find(lambda r: r["recommended_payment_method"] == "full_payment" and r["affordability_status"] == "affordable_now")
R_INST = find(lambda r: r["recommended_payment_method"] == "installments")
R_PART = find(lambda r: r["recommended_payment_method"] == "partial_payment")
R_WAIT = find(lambda r: r["recommended_payment_method"] == "wait")
R_NOT  = find(lambda r: r["recommended_payment_method"] == "not_recommended")
R_CHG  = find(lambda r: r["spending_changes_needed"] not in ("", "none"))

import csv as _c0
_rq0 = {r["request_id"]: r for r in _c0.DictReader(open(ROOT / "dataset" / "requests.csv"))}
_pf0 = {p["user_id"]: p for p in _c0.DictReader(open(ROOT / "dataset" / "financial_profiles.csv"))}
R_NOFULL = next(r["request_id"] for r in base
                if "full_payment" not in _pf0[_rq0[r["request_id"]]["user_id"]]["payment_methods_user_will_consider"])
tmp = tempfile.mkdtemp(prefix="riesgo_calib_")
print(f"mutaciones en {tmp}\n")
ok = []

ok.append(mut("columnas_desordenadas", "columns", lambda rs: rs,
              header=[COLS[1], COLS[0]] + COLS[2:]))
ok.append(mut("fila_borrada", "rowcount", lambda rs: rs[:-1]))
ok.append(mut("request_id_duplicado", "dup_id",
              lambda rs: (rs.__setitem__(1, {**rs[1], "request_id": rs[0]["request_id"]}), rs)[1]))
ok.append(mut("safe_negativo", "safe_range",
              lambda rs: rs.__setitem__(0, {**rs[0], "amount_safe_to_pay": "-1"}) or rs))
ok.append(mut("safe_sobre_requested", "safe_range",
              lambda rs: rs.__setitem__(idx[R_FULL], {**rs[idx[R_FULL]], "amount_safe_to_pay": "999999999"}) or rs))
ok.append(mut("safe_no_numerico", "safe_nan",
              lambda rs: rs.__setitem__(0, {**rs[0], "amount_safe_to_pay": "abc"}) or rs))
ok.append(mut("status_fuera_de_dominio", "status_domain",
              lambda rs: rs.__setitem__(0, {**rs[0], "affordability_status": "maybe"}) or rs))
ok.append(mut("metodo_fuera_de_dominio", "method_domain",
              lambda rs: rs.__setitem__(0, {**rs[0], "recommended_payment_method": "crypto"}) or rs))
ok.append(mut("fecha_malformada", "date_format",
              lambda rs: rs.__setitem__(idx[R_WAIT], {**rs[idx[R_WAIT]], "earliest_date_for_full_payment": "15/08/2026"}) or rs))
ok.append(mut("affordable_now_sin_earliest", "now_vs_earliest",
              lambda rs: rs.__setitem__(idx[R_FULL], {**rs[idx[R_FULL]], "earliest_date_for_full_payment": ""}) or rs))
ok.append(mut("plan_desordenado", "plan_order",
              lambda rs: rs.__setitem__(idx[R_INST], {**rs[idx[R_INST]],
                  "payment_plan": "|".join(reversed(rs[idx[R_INST]]["payment_plan"].split("|")))}) or rs))
ok.append(mut("plan_monto_cero", "plan_amount",
              lambda rs: rs.__setitem__(idx[R_FULL], {**rs[idx[R_FULL]],
                  "payment_plan": rs[idx[R_FULL]]["payment_plan"].split(":")[0] + ":0"}) or rs))
ok.append(mut("plan_mal_formado", "plan_format",
              lambda rs: rs.__setitem__(idx[R_FULL], {**rs[idx[R_FULL]], "payment_plan": "2026-01-01"}) or rs))
ok.append(mut("not_recommended_con_plan", "plan_vs_method",
              lambda rs: rs.__setitem__(idx[R_NOT], {**rs[idx[R_NOT]], "payment_plan": "2026-01-01:10"}) or rs))
ok.append(mut("metodo_sin_plan", "plan_vs_method",
              lambda rs: rs.__setitem__(idx[R_FULL], {**rs[idx[R_FULL]], "payment_plan": "none"}) or rs))
ok.append(mut("cuota_no_coincide_opcion", "option_match",
              lambda rs: rs.__setitem__(idx[R_INST], {**rs[idx[R_INST]],
                  "payment_plan": "|".join(t.split(":")[0] + ":1" for t in rs[idx[R_INST]]["payment_plan"].split("|"))}) or rs))
ok.append(mut("parcial_no_suma", "plan_sum",
              lambda rs: rs.__setitem__(idx[R_PART], {**rs[idx[R_PART]],
                  "payment_plan": rs[idx[R_PART]]["payment_plan"].rsplit(":", 1)[0] + ":1"}) or rs))
ok.append(mut("cambio_sobre_evento_fantasma", "change_ghost",
              lambda rs: rs.__setitem__(idx[R_CHG], {**rs[idx[R_CHG]], "spending_changes_needed": "stop:event_999999"}) or rs))
ok.append(mut("cambio_sobre_evento_fijo", "change_flex",
              lambda rs: rs.__setitem__(idx[R_CHG], {**rs[idx[R_CHG]], "spending_changes_needed": "stop:event_01"}) or rs))
ok.append(mut("cambio_mal_formado", "changes_format",
              lambda rs: rs.__setitem__(idx[R_CHG], {**rs[idx[R_CHG]], "spending_changes_needed": "stop"}) or rs))
ok.append(mut("explicacion_vacia", "explain_empty",
              lambda rs: rs.__setitem__(0, {**rs[0], "decision_explanation": ""}) or rs))
ok.append(mut("not_affordable_con_earliest", "notaff_con_earliest",
              lambda rs: rs.__setitem__(idx[R_NOT], {**rs[idx[R_NOT]], "earliest_date_for_full_payment": "2026-01-01"}) or rs))
# La mutacion anterior cayo en un usuario que SI acepta installments: el auditor
# disparo option_match, no eligibility. Se elige un request cuyo perfil lo excluye.
import csv as _csv
_reqs = {r["request_id"]: r for r in _csv.DictReader(open(ROOT / "dataset" / "requests.csv"))}
_prof = {p["user_id"]: p for p in _csv.DictReader(open(ROOT / "dataset" / "financial_profiles.csv"))}
R_NOINST = next(r["request_id"] for r in base
                if "installments" not in _prof[_reqs[r["request_id"]]["user_id"]]["payment_methods_user_will_consider"])
ok.append(mut("metodo_no_aceptado", "method_eligibility",
              lambda rs: rs.__setitem__(idx[R_NOINST], {**rs[idx[R_NOINST]],
                  "recommended_payment_method": "installments", "affordability_status": "affordable_with_plan",
                  "payment_plan": "2026-01-01:10"}) or rs))
ok.append(mut("wait_sin_full_payment", "method_eligibility",
              lambda rs: rs.__setitem__(idx[R_NOFULL], {**rs[idx[R_NOFULL]],
                  "recommended_payment_method": "wait", "affordability_status": "affordable_later",
                  "payment_plan": "2026-01-01:10", "earliest_date_for_full_payment": "2026-01-01"}) or rs))

print(f"\ncalibracion: {sum(ok)}/{len(ok)} clases se pusieron ROJAS")
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(ok) else 1)
