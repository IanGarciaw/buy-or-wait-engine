"""Caché en disco de A3. Reanudar NUNCA repite una llamada ya hecha.

Un archivo por tipo (images.json, messages.json), clave = image_id / message_id.
215 mensajes + 16 imágenes es chico: no hace falta un archivo por clave, un único
JSON reescrito completo tras cada lote basta y es trivial de inspeccionar a mano.

usage.jsonl acumula una línea por llamada real al modelo (nunca por hit de caché),
con exactamente los campos que evaluation/ necesita para el usage_report.md.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
IMAGES_CACHE = CACHE_DIR / "images.json"
MESSAGES_CACHE = CACHE_DIR / "messages.json"
USAGE_LOG = CACHE_DIR / "usage.jsonl"

_lock = threading.Lock()


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: Path, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def load_images_cache() -> dict:
    return _load(IMAGES_CACHE)


def save_images_cache(data: dict) -> None:
    with _lock:
        _save(IMAGES_CACHE, data)


def load_messages_cache() -> dict:
    return _load(MESSAGES_CACHE)


def save_messages_cache(data: dict) -> None:
    with _lock:
        _save(MESSAGES_CACHE, data)


def log_usage(record: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
