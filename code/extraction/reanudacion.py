"""¿El mensaje REANUDA un ingreso recurrente con importe explícito?

Hermano de `extraction/alcance.py` y con la misma frontera: el texto del mensaje
sigue siendo dato no confiable; lo único que cruza es un símbolo de un conjunto
cerrado (`contracts.FACT_RESUME`). Aquí no se lee ninguna orden.

Por qué existe: el corpus manda dos hechos en una frase —

    "Regular salary of INR 62000 resumes on 2025-05-15."

el IMPORTE recurrente y la FECHA de reanudación. El extractor la tipa
`income_date_change` y el consumidor de ese kind sólo lee la fecha, así que el
importe que el mensaje sí sostiene se tira. El mismo texto con EUR 2717 (user_14)
cayó en `income_change`, conservó el importe y la verdad de campo lo respalda: la
diferencia entre los dos era la etiqueta, no la semántica.

NO es una reanudación (y por eso no entra aquí): un pago puntual, un atraso, un
bono, un reembolso, un crédito pendiente o cualquier ingreso ambiguo.
"""
from __future__ import annotations

# El ingreso declarado es RECURRENTE: sueldo/nómina regular, no un pago suelto.
RECURRENTE = (
    "regular salary", "monthly salary", "salary resumes", "regular pay",
    "recurring salary", "gaji rutin", "gaji bulanan", "gaji reguler",
)

# Se REANUDA: vuelve a correr algo que estaba parado.
REANUDA = (
    "resumes", "resume on", "will resume", "resumes on", "restarts", "will restart",
    "dilanjutkan kembali", "dimulai kembali", "kembali dibayarkan", "berlanjut kembali",
)

# Vocabulario que descalifica: el dinero del que habla NO es la nómina recurrente.
NO_ES_NOMINA_RECURRENTE = (
    "bonus", "commission", "refund", "reimbursement", "reversal", "arrears",
    "back pay", "backpay", "one-off", "one off", "one-time", "lump sum",
    "pending credit", "not yet", "under review", "still being", "may change",
    "bonus", "komisi", "pengembalian dana", "sekali", "tunggakan",
)


def _hay(t: str, palabras) -> bool:
    return any(w in t for w in palabras)


def clasificar(texto: str, kind: str, amount, effective_date) -> str:
    """-> "RESUME_WITH_AMOUNT" | "".

    Las cuatro condiciones del encargo, todas exigidas a la vez:
      1. importe explícito            -> `amount` no es None
      2. recurrencia explícita        -> léxico RECURRENTE
      3. fecha de reanudación         -> léxico REANUDA + `effective_date`
      4. nada que descalifique el dinero como nómina recurrente
    """
    if kind not in ("income_date_change", "income_change"):
        return ""
    if amount is None or effective_date is None:
        return ""
    t = " ".join((texto or "").lower().split())
    if _hay(t, NO_ES_NOMINA_RECURRENTE):
        return ""
    if not _hay(t, RECURRENTE):
        return ""
    if not _hay(t, REANUDA):
        return ""
    return "RESUME_WITH_AMOUNT"
