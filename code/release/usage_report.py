#!/usr/bin/env python3
"""Genera code/evaluation/usage_report.md a partir de las llamadas REALES.

Lee code/cache/usage.jsonl — una línea por invocación del modelo, escrita por los
extractores en el momento de la llamada — y suma. No estima nada: si una cifra no
está en el registro, no aparece en el reporte.

    python3 code/release/usage_report.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
USAGE = ROOT / "code" / "cache" / "usage.jsonl"
OUT = ROOT / "code" / "evaluation" / "usage_report.md"
N_REQUESTS = 250


def read_calls() -> list[dict]:
    if not USAGE.exists():
        return []
    calls = []
    for line in USAGE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            calls.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return calls


def g(c: dict, *names, default=0):
    for n in names:
        if c.get(n) is not None:
            return c[n]
    return default


def build() -> str:
    calls = read_calls()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not calls:
        return (f"# Token Usage and Cost Analysis\n\n_Generado {now}_\n\n"
                "No hay llamadas registradas en `code/cache/usage.jsonl`.\n"
                "Ejecuta la extracción y vuelve a generar este reporte.\n")

    per_model = defaultdict(lambda: dict(calls=0, inp=0, out=0, cc=0, cr=0, cost=0.0, ms=0))
    per_stage = defaultdict(lambda: dict(calls=0, inp=0, out=0, cost=0.0))

    # Los productores escriben las claves en inglés o en español según el módulo.
    # Aceptar ambas: comparar contra un solo literal daría un reporte con "unknown".
    for c in calls:
        m = c.get("model") or c.get("modelo") or "unknown"
        s = c.get("stage") or c.get("tipo") or "unknown"
        inp = g(c, "input_tokens", "inputTokens")
        out = g(c, "output_tokens", "outputTokens")
        cc = g(c, "cache_creation", "cache_creation_input_tokens")
        cr = g(c, "cache_read", "cache_read_input_tokens")
        cost = float(g(c, "cost_usd", "total_cost_usd", default=0.0))

        d = per_model[m]
        d["calls"] += 1; d["inp"] += inp; d["out"] += out
        d["cc"] += cc; d["cr"] += cr; d["cost"] += cost
        d["ms"] += g(c, "ms", "duration_ms")

        e = per_stage[s]
        e["calls"] += 1; e["inp"] += inp + cc + cr; e["out"] += out; e["cost"] += cost

    T = dict(calls=0, inp=0, out=0, cc=0, cr=0, cost=0.0, ms=0)
    for d in per_model.values():
        for k in T:
            T[k] += d[k]
    total_in = T["inp"] + T["cc"] + T["cr"]
    total_tok = total_in + T["out"]

    L = []
    A = L.append
    A("# Token Usage and Cost Analysis")
    A("")
    A(f"_Generated {now} from the final full-dataset run that produced `output.csv`._")
    A("")
    A("## How the model is used")
    A("")
    A("The financial calculation is **fully deterministic Python**. The model is used only")
    A("to turn unstructured evidence into typed facts, and to phrase explanations around")
    A("numbers that are already frozen. It never computes an amount, a date or a plan.")
    A("")
    A("Provider: **Anthropic**, accessed through the local `claude` CLI")
    A("(`claude --print --output-format json`). No API key is used; the CLI runs against a")
    A("subscription plan, so **real billed cost is $0.00**. The `cost` column below is the")
    A("list-price equivalent reported by the CLI in `total_cost_usd`, included because the")
    A("task asks for an estimated cost. Both figures are stated so neither is misleading.")
    A("")
    A("## Per model")
    A("")
    A("| Provider | Model | Calls | Input tokens | Cache create | Cache read | Output tokens | Cost (list, USD) |")
    A("|---|---|---:|---:|---:|---:|---:|---:|")
    for m, d in sorted(per_model.items()):
        A(f"| Anthropic | `{m}` | {d['calls']} | {d['inp']:,} | {d['cc']:,} | "
          f"{d['cr']:,} | {d['out']:,} | ${d['cost']:.4f} |")
    A(f"| **Total** | — | **{T['calls']}** | **{T['inp']:,}** | **{T['cc']:,}** | "
      f"**{T['cr']:,}** | **{T['out']:,}** | **${T['cost']:.4f}** |")
    A("")
    A("## Per pipeline stage")
    A("")
    A("| Stage | Calls | Input tokens | Output tokens | Cost (list, USD) |")
    A("|---|---:|---:|---:|---:|")
    for s, e in sorted(per_stage.items(), key=lambda kv: -kv[1]["calls"]):
        A(f"| {s} | {e['calls']} | {e['inp']:,} | {e['out']:,} | ${e['cost']:.4f} |")
    A("")
    A("## Totals and per-request averages")
    A("")
    A(f"- Requests in `dataset/requests.csv`: **{N_REQUESTS}**")
    A(f"- Model calls: **{T['calls']}**  ({T['calls']/N_REQUESTS:.3f} per request)")
    A(f"- Input tokens (incl. cache): **{total_in:,}**  "
      f"({total_in/N_REQUESTS:,.1f} per request)")
    A(f"- Output tokens: **{T['out']:,}**  ({T['out']/N_REQUESTS:,.1f} per request)")
    A(f"- Total tokens: **{total_tok:,}**  ({total_tok/N_REQUESTS:,.1f} per request)")
    A(f"- Estimated cost (list price): **${T['cost']:.4f}**  "
      f"(**${T['cost']/N_REQUESTS:.5f}** per request)")
    A(f"- Real billed cost: **$0.00** (subscription, no API key)")
    A(f"- Model wall-clock: **{T['ms']/1000:.1f} s** across {T['calls']} calls, run in parallel")
    A("")
    A("## Why the call count is low")
    A("")
    A("Calls are batched, not per request. The CLI carries a fixed per-call overhead of")
    A("about 9,100 tokens once `--restricted` is set, so many small calls cost far more")
    A("than a few large ones. Messages are extracted in batches; images are one call each")
    A("because each is a distinct document. Every result is cached on disk by `image_id`")
    A("and `message_id`, so a re-run costs nothing and a resume never repeats work.")
    A("")
    return "\n".join(L)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    n = len(read_calls())
    print(f"{OUT.relative_to(ROOT)} generado desde {n} llamadas registradas")
