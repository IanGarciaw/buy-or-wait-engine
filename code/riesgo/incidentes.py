#!/usr/bin/env python3
"""Captura TODOS los incidentes del verificador de la corrida completa. Solo lectura."""
import sys, collections, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import main, loader  # noqa

print("componentes enganchados:",
      {"build_view": bool(main.build_view), "decide": bool(main.decide),
       "load_facts": bool(main.load_facts), "verify": bool(main.verify)})

ds = loader.load()
facts = main.load_facts(ds)
decisions, counts, incidents = main.run(ds.requests, ds, facts, quiet=True)
print("counts:", counts)
print("total incidentes:", len(incidents))

cat = collections.Counter()
reqs_por_cat = collections.defaultdict(set)
for i in incidents:
    m = re.match(r"(request_\d+): (?:(INFO|WARN|BLOCK)\[([a-z_]+)\]|(.*))", i)
    if m and m.group(2):
        k = f"{m.group(2)}[{m.group(3)}]"
    elif m:
        k = "OTRO: " + m.group(4)[:60]
    else:
        k = "SIN PARSEAR: " + i[:60]
    cat[k] += 1
    if m:
        reqs_por_cat[k].add(m.group(1))
for k, v in cat.most_common():
    print(f"  {v:>4}  {k}   ({len(reqs_por_cat[k])} requests)")

# fallos duros del motor (camino de respaldo)
duros = [i for i in incidents if "motor fall" in i or "verificador fall" in i]
print("fallos duros del motor/verificador:", len(duros))
for i in duros[:10]:
    print("   ", i)

# filas con explicacion vacia antes del respaldo _explain
vacias = [d.request_id for d in decisions if not d.decision_explanation]
print("explicaciones vacias tras el pipeline:", len(vacias))

Path(ROOT / "code" / "riesgo" / "incidentes.txt").write_text("\n".join(incidents), encoding="utf-8")
print("-> code/riesgo/incidentes.txt")
