#!/usr/bin/env python3
"""Tabla de auditoría de las 16 imágenes. Un humano la revisa en un minuto.

    python3 code/extraction/revisar_imagenes.py             # usa caché si existe
    python3 code/extraction/revisar_imagenes.py --refresh    # fuerza re-extracción

Columnas: event_id, monto extraído, label leído, confianza, moneda esperada (la del
dataset — nunca la que el modelo diga, porque ImageFact no tiene ese campo).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # code/extraction
CODE_ROOT = HERE.parent                         # code/
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

import loader                                    # noqa: E402
from contracts import fmt_amount                 # noqa: E402

from extraction import images as _images         # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    ds = loader.load()
    facts = _images.build_image_facts(ds, refresh=args.refresh)
    events_by_id = {e.event_id: e for evs in ds.events_by_user.values() for e in evs}

    rows = []
    for img in ds.images:
        ev = events_by_id.get(img.related_event_id)
        fact = facts.get(img.related_event_id)
        monto = fmt_amount(fact.amount) if fact and fact.amount is not None else "—"
        label = (fact.label if fact else "")[:42]
        conf = f"{fact.confidence:.2f}" if fact else "0.00"
        moneda = ev.currency if ev else "?"
        rows.append((img.image_id, img.related_event_id or "—", monto, label, conf, moneda))

    headers = ("image_id", "event_id", "monto", "label", "conf.", "moneda_esp.")
    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]

    def fmt_row(r):
        return "  ".join(str(c).ljust(w) for c, w in zip(r, widths))

    print(fmt_row(headers))
    print(fmt_row(tuple("-" * w for w in widths)))
    for r in rows:
        print(fmt_row(r))

    bajas = [r for r in rows if float(r[4]) < 0.60]
    sin_monto = [r for r in rows if r[2] == "—"]
    print(f"\n{len(rows)} imágenes · confianza<0.60: "
          f"{', '.join(r[0] for r in bajas) if bajas else 'ninguna'}"
          f" · sin monto: {', '.join(r[0] for r in sin_monto) if sin_monto else 'ninguna'}")


if __name__ == "__main__":
    main()
