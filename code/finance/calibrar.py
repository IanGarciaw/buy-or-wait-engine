#!/usr/bin/env python3
"""Marcador de A1 contra los 25 samples resueltos. Éste es el KPI.

    python3 code/finance/calibrar.py            # marcador con los parámetros vigentes
    python3 code/finance/calibrar.py --barrer   # barrido de estimadores y umbrales

Los samples son de usuarios que NO están en la evaluación. Sirven para DESCUBRIR
reglas generales — jamás para memorizar respuestas. No hay ninguna rama en el motor
que mire un request_id, un user_id ni una constante nacida de un caso concreto.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import loader                                        # noqa: E402
from finance import params                           # noqa: E402


def _num(s: str) -> Decimal | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def cargar_hechos() -> dict:
    """Los hechos de A3 si existen. Si no, {} — el motor funciona igual."""
    try:
        from extraction.facts import load_facts
        return load_facts(loader.load())
    except Exception as exc:
        print(f"(sin hechos de A3: {type(exc).__name__}: {exc})")
        return {}


def run(facts: dict | None = None, verbose: bool = True) -> tuple[int, int, int]:
    import importlib
    from finance import series as series_mod, view as view_mod
    importlib.reload(series_mod)
    importlib.reload(view_mod)

    ds = loader.load()
    truth = {r["request_id"]: r for r in loader.load_samples()}
    reqs = loader.sample_requests()
    facts = facts if facts is not None else _HECHOS

    ok_safe = ok_exact = ok_early = ok5 = ok10 = 0
    errs = []
    rows = []
    for req in reqs:
        t = truth[req.request_id]
        exp = _num(t["amount_safe_to_pay"]) or Decimal(0)
        exp_e = (t["earliest_date_for_full_payment"] or "").strip()
        try:
            v = view_mod.build_view(ds, req, facts)
            got, got_e = v.amount_safe_today, v.earliest_full_payment
        except Exception as exc:
            rows.append((req.request_id, "ERROR", str(exc)[:60], "", ""))
            continue

        tol = abs(exp) * Decimal("0.01")
        hit = abs(got - exp) <= max(tol, Decimal("0.01"))
        ok_safe += hit
        ok_exact += abs(got - exp) <= Decimal("0.5")
        got_es = got_e.isoformat() if got_e else ""
        he = got_es == exp_e
        ok_early += he
        rel = (abs(got - exp) / exp * 100) if exp else \
            (Decimal(0) if got == 0 else Decimal(100))
        errs.append(rel)
        ok5 += rel <= 5
        ok10 += rel <= 10
        rows.append((req.request_id, f"{float(exp):,.2f}", f"{float(got):,.2f}",
                     ("OK " if hit else f"x {float(rel):.1f}%"),
                     f"{exp_e or '-'} / {got_es or '-'}{'' if he else '  <-'}"))

    err_medio = sum(errs) / Decimal(len(errs)) if errs else Decimal(0)
    se = sorted(errs)
    err_med = se[len(se) // 2] if se else Decimal(0)
    if verbose:
        print(f"{'request':<12}{'esperado':>18}{'obtenido':>18}  {'safe':<10}earliest esp/obt")
        for r in rows:
            print(f"{r[0]:<12}{r[1]:>18}{r[2]:>18}  {r[3]:<10}{r[4]}")
        print()
        print(f"safe dentro de ±1%: {ok_safe}/25   (exacto: {ok_exact}/25)")
        print(f"safe dentro de ±5%: {ok5}/25    ±10%: {ok10}/25")
        print(f"error relativo medio: {float(err_medio):.1f}%   mediano: {float(err_med):.1f}%")
        print(f"earliest exacto:    {ok_early}/25")
        print(f"estimador={params.ESTIMATOR} lookback={params.LOOKBACK} "
              f"min_occ={params.MIN_OCCURRENCES}/{params.MIN_OCCURRENCES_INCOME} "
              f"tol={params.INTERVAL_TOL} agree={params.INTERVAL_AGREE} "
              f"stale={params.STALE_FACTOR} day0={params.INCLUDE_DAY_ZERO}")
    return ok_safe, ok_early, ok_exact, ok5, err_medio


def barrer() -> None:
    """Barrido honesto: se mueve un eje a la vez y se reporta el número."""
    ejes = {
        "ESTIMATOR": ["mean", "median", "p75", "max", "last"],
        "LOOKBACK": [3, 4, 6, 8, 12, 0],
        "MIN_OCCURRENCES": [2, 3, 4],
        "MIN_OCCURRENCES_INCOME": [2, 3],
        "INTERVAL_TOL": [0.2, 0.34, 0.5],
        "INTERVAL_AGREE": [0.5, 0.6, 0.75, 1.0],
        "STALE_FACTOR": [1.0, 1.2, 1.6, 2.5, 99],
        "VARIABLE_MULT": [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2],
        "INCLUDE_DAY_ZERO": [True, False],
        "FACT_ENMIENDA_SIN_OBJETIVO_ES_INGRESO": [True, False],
        "FACT_NUEVO_RECURRENTE": ["gasto", "ingreso", "ignorar"],
        "FACT_NOT_YET_CASH_SIN_OBJETIVO": ["ignorar", "suprimir_ingreso",
                                           "suprimir_variable"],
        "INCOME_ESTIMATOR": ["last", "mean", "median"],
        "FACT_INGRESO_TERMINADO_CON_OBJETIVO": ["descartar_evento", "terminar_ingreso"],
        "FACT_MIN_CONFIDENCE": [0.0, 0.8, 0.9],
    }
    base = {k: getattr(params, k) for k in ejes}
    for eje, vals in ejes.items():
        print(f"\n── {eje} (resto en {', '.join(f'{k}={v}' for k, v in base.items() if k != eje)})")
        for v in vals:
            for k, b in base.items():
                setattr(params, k, b)
            setattr(params, eje, v)
            s, e, x, c5, em = run(facts=_HECHOS, verbose=False)
            print(f"   {eje}={str(v):<16} safe={s:>2}/25  ±5%={c5:>2}/25  "
                  f"earliest={e:>2}/25  err.medio={float(em):>6.1f}%")
    for k, b in base.items():
        setattr(params, k, b)


_HECHOS: dict = {}

if __name__ == "__main__":
    _HECHOS = {} if "--sin-hechos" in sys.argv else cargar_hechos()
    if "--barrer" in sys.argv:
        barrer()
    else:
        run()
