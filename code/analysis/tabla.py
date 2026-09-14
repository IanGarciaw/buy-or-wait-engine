"""Tabla maestra: 25 samples, esperado vs obtenido, y el min_balance objetivo."""
from __future__ import annotations
import sys, os
from decimal import Decimal
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import loader
from finance import view as fv
from extraction.facts import load_facts

def main():
    ds = loader.load()
    facts = load_facts(ds)
    samples = {r["request_id"]: r for r in loader.load_samples()}
    reqs = loader.sample_requests()
    print(f"{'req':<11}{'user':<9}{'cur':<4}{'balance':>13}{'min_keep':>11}{'requested':>14}"
          f"{'exp_safe':>14}{'obt_safe':>14}{'min_obt':>14}{'min_target':>14}{'delta':>13}  {'earl_exp':<11}{'earl_obt':<11}")
    for req in reqs:
        row = samples[req.request_id]
        prof = ds.profiles[req.user_id]
        v = fv.build_view(ds, req, facts)
        exp = Decimal(row["amount_safe_to_pay"])
        obt = v.amount_safe_today
        lo = v.forecast.min_balance
        tgt = exp + prof.minimum_balance_to_keep
        cap = " (=req)" if exp == req.requested_amount else ""
        ok = "OK" if abs(exp-obt) <= max(Decimal("0.01"), abs(exp)*Decimal("0.01")) else "XX"
        print(f"{req.request_id:<11}{req.user_id:<9}{prof.home_currency:<4}"
              f"{prof.current_available_balance:>13.2f}{prof.minimum_balance_to_keep:>11.2f}"
              f"{req.requested_amount:>14.2f}{exp:>14.2f}{obt:>14.2f}{lo:>14.2f}{tgt:>14.2f}"
              f"{lo-tgt:>13.2f}  {row['earliest_date_for_full_payment']:<11}"
              f"{(v.earliest_full_payment or ''):<11} {ok}{cap}")

main()
