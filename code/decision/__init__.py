"""Motor de decisión (A2). Entrada única: `decide`.

    from decision import decide
    decision = decide(view, req, options)          # -> contracts.Decision

La fila del CSV la escribe `contracts.to_row(decision)`: A2 no formatea salida.
"""
from .engine import decide

__all__ = ["decide"]
