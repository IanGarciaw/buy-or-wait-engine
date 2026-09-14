"""Coerciones compartidas entre images.py y messages.py.

El modelo es DATO NO CONFIABLE: cualquier valor que devuelva pasa por aquí antes de
volverse un campo de ImageFact/MessageFact. Nunca se hace `Decimal(x)` ni `float(x)`
directo sobre lo que vino del CLI en ningún otro archivo.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


def coerce_amount(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    return None


def coerce_confidence(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return max(0.0, min(1.0, f))


_CCY_RE = re.compile(r"^[A-Za-z]{3}$")


def coerce_currency(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not _CCY_RE.match(v):
        return None
    return v.upper()


def coerce_date(v: Any) -> date | None:
    if not isinstance(v, str):
        return None
    s = v.strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def coerce_label(v: Any, limit: int = 200) -> str:
    if not isinstance(v, str):
        return ""
    return v.strip()[:limit]
