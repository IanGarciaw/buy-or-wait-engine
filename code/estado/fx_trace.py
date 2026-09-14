#!/usr/bin/env python3
"""FRENTE 1 — FX. Instrumenta loader.convert SIN tocar producción y corre los 250.

Registra, por llamada: quién llama, monto, par, fecha pedida, camino tomado
(exacta | invertida | cercana_misma_direccion | cercana_invertida | SIN_CONVERTIR),
la fecha de la tasa realmente usada y el resultado.

    python3 code/estado/fx_trace.py            # corre los 250 con hechos de caché
"""
from __future__ import annotations

import json
import sys
import traceback
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

import loader  # noqa: E402

CALLS: list[dict] = []
_orig = loader.convert


def traced(amount, frm, to, on, rates):
    # Reimplementa la MISMA lógica para saber qué rama gana, luego delega al original.
    path, rate_date, rate = None, None, None
    if frm == to:
        path = "misma_moneda"
    else:
        r = rates.get((on, frm, to))
        if r is not None:
            path, rate_date, rate = "exacta", on, r
        else:
            inv = rates.get((on, to, frm))
            if inv:
                path, rate_date, rate = "invertida", on, inv
            else:
                cands = [(d, v) for (d, f, t), v in rates.items()
                         if f == frm and t == to and d <= on]
                if cands:
                    d, v = max(cands, key=lambda kv: kv[0])
                    path, rate_date, rate = "cercana_misma_direccion", d, v
                else:
                    cands = [(d, v) for (d, f, t), v in rates.items()
                             if f == to and t == frm and d <= on]
                    if cands:
                        d, v = max(cands, key=lambda kv: kv[0])
                        path, rate_date, rate = "cercana_invertida", d, v
                    else:
                        path = "SIN_CONVERTIR"
    out = _orig(amount, frm, to, on, rates)
    stack = [f"{f.filename.split('/')[-1]}:{f.lineno}:{f.name}"
             for f in traceback.extract_stack()[:-1]]
    caller = next((s for s in reversed(stack)
                   if not s.startswith("fx_trace")), "?")
    CALLS.append({
        "caller": caller, "amount": str(amount), "frm": frm, "to": to,
        "on": str(on), "path": path,
        "rate_date": str(rate_date) if rate_date else None,
        "rate": str(rate) if rate is not None else None,
        "result": str(out),
        "unchanged": bool(out == amount and frm != to),
    })
    return out


loader.convert = traced
import main as _main  # noqa: E402  (importa DESPUÉS del parche)

# finance.view hace `import loader` y llama loader.convert -> ve el parche.
# main.py también. extraction no convierte.


def run():
    ds = loader.load()
    facts = {"images": {}, "messages": {}}
    if _main.load_facts:
        facts = _main.load_facts(ds)
    decisions, counts, incidents = _main.run(ds.requests, ds, facts, quiet=True)
    return ds, facts, decisions, counts, incidents


if __name__ == "__main__":
    ds, facts, decisions, counts, incidents = run()
    print(f"decisiones={len(decisions)} counts={counts} incidentes={len(incidents)}")
    print(f"llamadas a convert: {len(CALLS)}")
    from collections import Counter
    print("por camino:", Counter(c["path"] for c in CALLS))
    print("por caller:", Counter(c["caller"] for c in CALLS))
    sin = [c for c in CALLS if c["path"] == "SIN_CONVERTIR"]
    print(f"SIN_CONVERTIR: {len(sin)}")
    for c in sin[:40]:
        print("   ", c)
    inv = [c for c in CALLS if c["path"] in ("invertida", "cercana_invertida",
                                             "cercana_misma_direccion")]
    print(f"caminos de respaldo (no exacta): {len(inv)}")
    for c in inv[:40]:
        print("   ", c)
    (HERE / "fx_calls.json").write_text(json.dumps(CALLS, indent=1), encoding="utf-8")
    print(f"-> {HERE / 'fx_calls.json'}")
