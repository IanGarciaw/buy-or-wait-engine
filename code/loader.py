"""Carga los 9 CSV a los tipos de contracts.py. Sólo lectura: dataset/ no se toca.

El vacío no es ausencia: un `amount` en blanco es None (hay que sacarlo de una imagen),
nunca cero. Un `settlement_date` en blanco es None, no la fecha del evento.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from contracts import (Dataset, Event, ImageRef, Message, PaymentOption, Profile,
                       Request)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "dataset"


def _d(s: str | None) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _n(s: str | None) -> Decimal | None:
    """Nulo, vacío y malformado son cosas distintas. Sólo el vacío devuelve None."""
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _list(s: str | None) -> tuple[str, ...]:
    s = (s or "").strip()
    return tuple(x.strip() for x in s.split("|") if x.strip()) if s else ()


def _bool(s: str | None) -> bool:
    return (s or "").strip().lower() in ("true", "1", "yes")


def _rows(name: str) -> list[dict]:
    with open(DATA / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load() -> Dataset:
    profiles = {}
    for r in _rows("financial_profiles.csv"):
        mim = (r.get("max_installment_months") or "").strip()
        profiles[r["user_id"]] = Profile(
            user_id=r["user_id"],
            home_currency=r["home_currency"],
            current_available_balance=_n(r["current_available_balance"]) or Decimal(0),
            minimum_balance_to_keep=_n(r["minimum_balance_to_keep"]) or Decimal(0),
            financial_priorities=_list(r.get("financial_priorities")),
            protect=_list(r.get("expense_categories_to_protect")),
            willing_reduce=_list(r.get("expense_categories_user_is_willing_to_reduce")),
            willing_stop=_list(r.get("expense_categories_user_is_willing_to_stop")),
            methods_considered=_list(r.get("payment_methods_user_will_consider")),
            max_installment_months=int(mim) if mim else None,
        )

    events_by_user: dict[str, list[Event]] = {}
    for r in _rows("financial_events.csv"):
        e = Event(
            event_id=r["event_id"], user_id=r["user_id"],
            event_type=r["event_type"], description=r.get("description", ""),
            category=r.get("category", ""), direction=r["direction"],
            amount=_n(r["amount"]),                 # None si viene vacío: va a imagen
            currency=r["currency"],
            event_date=_d(r["event_date"]),
            settlement_date=_d(r.get("settlement_date")),
            status=r["status"],
            linked_event_id=(r.get("linked_event_id") or "").strip() or None,
            flexibility=r.get("flexibility", ""),
            minimum_allowed_amount=_n(r.get("minimum_allowed_amount")),
        )
        events_by_user.setdefault(e.user_id, []).append(e)

    requests = tuple(
        Request(
            request_id=r["request_id"], user_id=r["user_id"],
            request_date=_d(r["request_date"]), request_type=r["request_type"],
            requested_amount=_n(r["requested_amount"]) or Decimal(0),
            desired_completion_date=_d(r["desired_completion_date"]),
            allows_partial_payment=_bool(r.get("allows_partial_payment")),
            request_text=r.get("request_text", ""),
        ) for r in _rows("requests.csv")
    )

    options: dict[str, list[PaymentOption]] = {}
    for r in _rows("request_payment_options.csv"):
        freq = (r.get("payment_frequency_days") or "").strip()
        o = PaymentOption(
            payment_option_id=r["payment_option_id"], request_id=r["request_id"],
            payment_method=r["payment_method"],
            payment_amount=_n(r["payment_amount"]) or Decimal(0),
            number_of_payments=int(r["number_of_payments"]),
            first_payment_date=_d(r["first_payment_date"]),
            payment_frequency_days=int(freq) if freq else None,
            financing_fee=_n(r.get("financing_fee")) or Decimal(0),
            total_payable_amount=_n(r.get("total_payable_amount")) or Decimal(0),
        )
        options.setdefault(o.request_id, []).append(o)

    messages: dict[str, list[Message]] = {}
    for r in _rows("messages.csv"):
        m = Message(
            message_id=r["message_id"], user_id=r["user_id"],
            request_id=(r.get("request_id") or "").strip() or None,
            related_event_id=(r.get("related_event_id") or "").strip() or None,
            sent_at=r.get("sent_at", ""), source_type=r.get("source_type", ""),
            message_text=r.get("message_text", ""),
        )
        messages.setdefault(m.user_id, []).append(m)

    images = tuple(
        ImageRef(
            image_id=r["image_id"], user_id=r["user_id"],
            request_id=(r.get("request_id") or "").strip() or None,
            related_event_id=(r.get("related_event_id") or "").strip() or None,
            path=str(DATA / "media" / "images" / f"{r['image_id']}.png"),
        ) for r in _rows("images.csv")
    )

    rates = {}
    for r in _rows("exchange_rates.csv"):
        rates[(_d(r["rate_date"]), r["from_currency"], r["to_currency"])] = \
            _n(r["rate"]) or Decimal(1)

    return Dataset(
        profiles=profiles,
        events_by_user={k: tuple(v) for k, v in events_by_user.items()},
        requests=requests,
        options_by_request={k: tuple(v) for k, v in options.items()},
        messages_by_user={k: tuple(v) for k, v in messages.items()},
        images=images, rates=rates,
    )


def load_samples() -> list[dict]:
    """Los 25 samples resueltos. SÓLO para medir — nunca para producir predicciones."""
    return _rows("sample_requests.csv")


def sample_requests() -> tuple[Request, ...]:
    """Los 25 samples como Request, para correr el pipeline sobre ellos."""
    return tuple(
        Request(
            request_id=r["request_id"], user_id=r["user_id"],
            request_date=_d(r["request_date"]), request_type=r["request_type"],
            requested_amount=_n(r["requested_amount"]) or Decimal(0),
            desired_completion_date=_d(r["desired_completion_date"]),
            allows_partial_payment=_bool(r.get("allows_partial_payment")),
            request_text=r.get("request_text", ""),
        ) for r in load_samples()
    )


def convert(amount: Decimal, frm: str, to: str, on: date,
            rates: dict) -> Decimal:
    """Convierte usando la fila de la fecha de liquidación y la dirección indicada."""
    if frm == to:
        return amount
    r = rates.get((on, frm, to))
    if r is not None:
        return amount * r
    inv = rates.get((on, to, frm))
    if inv:
        return amount / inv
    # Sin fila para esa fecha exacta: la más cercana anterior en esa dirección.
    cands = [(d, v) for (d, f, t), v in rates.items() if f == frm and t == to and d <= on]
    if cands:
        return amount * max(cands, key=lambda kv: kv[0])[1]
    cands = [(d, v) for (d, f, t), v in rates.items() if f == to and t == frm and d <= on]
    if cands:
        return amount / max(cands, key=lambda kv: kv[0])[1]
    return amount


if __name__ == "__main__":
    ds = load()
    print(f"perfiles={len(ds.profiles)} requests={len(ds.requests)} "
          f"eventos={sum(len(v) for v in ds.events_by_user.values())} "
          f"opciones={sum(len(v) for v in ds.options_by_request.values())} "
          f"mensajes={sum(len(v) for v in ds.messages_by_user.values())} "
          f"imagenes={len(ds.images)} tasas={len(ds.rates)}")
    sin_monto = [e.event_id for v in ds.events_by_user.values() for e in v
                 if e.amount is None]
    print(f"eventos sin monto (van a imagen): {len(sin_monto)} -> {sin_monto[:5]}")
