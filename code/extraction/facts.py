#!/usr/bin/env python3
"""Punto de entrada único de A3.

    from extraction.facts import load_facts
    facts = load_facts(ds)
    # -> {"images": {event_id: ImageFact}, "messages": {user_id: [MessageFact, ...]}}

Lee de caché (code/cache/images.json, code/cache/messages.json) si existe y sólo
llama al modelo para lo que falte. `refresh=True` ignora la caché y re-extrae todo.

    python3 code/extraction/facts.py             # corre todo, usa caché
    python3 code/extraction/facts.py --refresh    # fuerza re-extracción completa
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # code/extraction
CODE_ROOT = HERE.parent                         # code/
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from contracts import Dataset  # noqa: E402

from extraction import images as _images    # noqa: E402
from extraction import messages as _messages  # noqa: E402


def load_facts(ds: Dataset, refresh: bool = False) -> dict:
    image_facts = _images.build_image_facts(ds, refresh=refresh)
    message_facts = _messages.build_message_facts(ds, refresh=refresh)
    return {"images": image_facts, "messages": message_facts}


if __name__ == "__main__":
    import time

    import loader

    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignora la caché, re-extrae todo")
    args = ap.parse_args()

    ds = loader.load()
    t0 = time.monotonic()
    facts = load_facts(ds, refresh=args.refresh)
    dt = time.monotonic() - t0

    n_msgs = sum(len(v) for v in facts["messages"].values())
    print(f"imágenes: {len(facts['images'])}/{len(ds.images)}")
    n_total_msgs = sum(len(v) for v in ds.messages_by_user.values())
    print(f"mensajes: {n_msgs}/{n_total_msgs}")
    print(f"tiempo: {dt:.1f}s")
