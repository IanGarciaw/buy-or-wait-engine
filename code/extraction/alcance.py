"""Alcance semántico de una terminación de ingreso.

El extractor devuelve `kind == "income_ended"` para TRES declaraciones distintas que
el corpus mezcla bajo la misma etiqueta:

  1. "One household employment record has ended. The remaining confirmed monthly
     salary is X."                       -> terminó UNA fuente, otra SIGUE VIVA
  2. "Your employment has ended. There are no regular salary payments scheduled
     after the final settlement."        -> terminó TODO el ingreso DE EMPLEO
  3. "The current seasonal contract has ended. No off-season income or renewal has
     been confirmed."                    -> terminó TODO el ingreso DE EMPLEO

Tratarlas igual es lo que produce el sobre-alcance de RC5 (1 se aplica como si fuera
3) y el defecto de alcance de A2 (3 se aplica como si fuera nada).

Esta función NO lee órdenes: devuelve una etiqueta de `contracts.FACT_SCOPES`. El
texto del mensaje sigue siendo dato no confiable; lo único que cruza la frontera es
un símbolo de un conjunto cerrado.

El léxico es bilingüe porque el corpus lo es (inglés + indonesio). Es el mismo
idioma de `finance/params.INCOME_TERMINAL`: vocabulario con el que el dataset marca
un cierre, no una lista de casos.
"""
from __future__ import annotations

# "una de varias": el mensaje dice que terminó UN registro, no el empleo.
PARCIAL = (
    "one household employment record",
    "salah satu sumber pendapatan",
    "salah satu catatan",
)

# "lo que queda confirmado": el mensaje afirma que OTRO ingreso sigue vivo.
REMANENTE = (
    "remaining confirmed",
    "sisa gaji",
    "gaji yang tersisa",
)

# Fin declarado de la relación laboral o del contrato de trabajo.
FIN_EMPLEO = (
    "employment has ended",
    "employment relationship has ended",
    "contract has ended",
    "no regular salary payments",
    "no off-season income",
    "hubungan kerja anda telah berakhir",
    "hubungan kerja telah berakhir",
    "kontrak musiman saat ini telah berakhir",
    "kontrak telah berakhir",
    "tidak ada pembayaran gaji rutin",
    "belum ada pendapatan di luar musim",
)

# Fin declarado de TODO ingreso, sea o no de empleo. No hay ningún caso en el
# corpus; la rama existe porque la regla la exige y se prueba en sintético.
FIN_TODO_INGRESO = (
    "no income of any kind",
    "all sources of income have ended",
    "tidak ada pendapatan dalam bentuk apa pun",
    "semua sumber pendapatan telah berakhir",
)


def _hay(texto: str, palabras) -> bool:
    return any(w in texto for w in palabras)


def clasificar(texto: str, kind: str, target_event_id: str | None,
               source_type: str = "") -> str:
    """-> uno de contracts.FACT_SCOPES. Orden de precedencia de la regla A3."""
    if kind != "income_ended":
        return ""
    if target_event_id:
        return "EVENT"
    t = " ".join((texto or "").lower().split())
    if _hay(t, FIN_TODO_INGRESO):
        return "TRULY_GLOBAL"
    if _hay(t, PARCIAL) or _hay(t, REMANENTE):
        return "SOURCE"
    if _hay(t, FIN_EMPLEO):
        return "EMPLOYMENT_GLOBAL"
    # No hay frase que sostenga un alcance. Una terminación anunciada por el
    # patrón del empleador no se ignora —eso sería el defecto de A2— pero tampoco
    # se estira más allá del ingreso de empleo.
    if (source_type or "").lower() == "employer":
        return "EMPLOYMENT_GLOBAL"
    return ""
