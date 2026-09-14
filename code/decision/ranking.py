"""Los seis criterios del enunciado, en su orden. Ninguna función de utilidad propia.

  1. completa el pedido antes de desired_completion_date
  2. no requiere cambios de gasto
  3. minimiza el total pagado          <- aquí pesa financing_fee / total_payable_amount
  4. empieza antes
  5. usa menos pagos
  6. el payment_option_id más bajo

El criterio 3 es el que manda de verdad y el corpus lo prueba dos veces:
  · request_02 elige payment_option_05 (3 pagos, fee IDR 1,840,720) sobre
    payment_option_07 (18 pagos, fee IDR 6,442,519).
  · request_19 elige partial_payment (paga 39,660 exactos) sobre payment_option_53
    (2 cuotas, 41,246.40) aunque el plazo empieza el mismo día.
Y el 4 desempata el resto de request_19: partial empieza el 04-sep, wait esperaría al
15-sep pagando lo mismo.

Antes que los seis va el piso de 90 días. Desde RC4 (`e74110d`) NO es un desempate:
`mejor()` veta los planes que hunden el saldo bajo `minimum_balance_to_keep` y, si no queda
ninguno limpio, la respuesta es `not_recommended`. Las dos primeras llaves de `clave()`
(brecha>0, brecha) sobreviven para ordenar y para la traza, pero ya no eligen nada: sobre
la tupla que `mejor()` le pasa, ambas valen siempre 0. El enunciado dice tres veces que un
plan sólo es seguro si el saldo nunca baja del mínimo, y en la línea 189 dice qué hacer
cuando ninguno lo es.

El id no se compara como texto: 'payment_option_100' < 'payment_option_99' en
lexicográfico y eso invierte el criterio 6. Se compara el número.
Mutación falsadora: mover el total pagado delante de "sin cambios de gasto" pone en rojo
test_ranking.py::test_sin_cambios_gana_a_mas_barato.
"""
from __future__ import annotations

import re
from decimal import Decimal

from contracts import Request

from .candidates import Plan

_NUM = re.compile(r"(\d+)\s*$")


def _id_num(option_id: str | None) -> int:
    if not option_id:
        return 10 ** 9                     # un plan sin opción no gana por el criterio 6
    m = _NUM.search(option_id)
    return int(m.group(1)) if m else 10 ** 9


def clave(req: Request):
    def _k(p: Plan):
        return (
            1 if p.brecha > 0 else 0,                                # 0: el piso de 90 días
            p.brecha,                                                # 0b: el que menos rompe
            0 if p.last_day <= req.desired_completion_date else 1,   # 1
            len(p.changes),                                          # 2
            p.total_paid,                                            # 3
            p.first_day,                                             # 4
            len(p.payments),                                         # 5
            _id_num(p.option_id),                                    # 6
        )
    return _k


def mejor(planes: tuple[Plan, ...], req: Request) -> Plan | None:
    """El mejor plan SEGURO. Si ninguno lo es, no hay recomendación.

    El enunciado separa las dos cosas y no deja margen:

        "`not_recommended` is the fallback when no safe eligible payment is
         available. WHEN MORE THAN ONE ELIGIBLE PLAN IS SAFE, rank the plans in
         this order: ..."          (problem_statement.md, línea 189)

    El ranking de seis criterios opera SÓLO sobre planes seguros. Un plan que rompe
    `minimum_balance_to_keep` en el horizonte de 90 días no entra en esa lista: la
    respuesta es `not_recommended`.

    La puerta entró en RC4 (`e74110d`), no en RC5. Antes de RC4 la brecha era la
    primera llave del ORDEN pero nunca una puerta: un plan inseguro perdía contra
    cualquier limpio, y se recomendaba igual si no quedaba otro. El enunciado exige
    cero.

    MEDIDO EL 13-SEP SOBRE ESTE MISMO ÁRBOL (RC5, `8004018`). Quitando sólo el veto
    `brecha <= 0` de esta función y volviendo a correr `python3 code/main.py`,
    cambian **38 filas de 250**, las 38 de `not_recommended` a `installments`
    (y de `not_affordable` a `affordable_with_plan`). Ésas son las 38
    recomendaciones inseguras que esta puerta evita. Control del instrumento: sin la
    mutación, el `output.csv` regenerado sale idéntico byte a byte al entregado
    (sha256 9167bb84...05fc77), así que el contador sabe distinguir 0 de 38.

    La cifra de **46** que estuvo en este docstring y en los mensajes de commit de
    RC4 y RC5 NO se reproduce en ningún árbol del repo: sobre RC3 (`d9b781d`)
    añadir el veto mueve 13 filas; sobre RC4 (`e74110d`) quitarlo mueve 39 (38 a
    `installments` y 1 a `partial_payment`); sobre RC5, 38. Los mensajes de commit
    no se pueden corregir sin mover los tags, así que la corrección queda aquí y en
    `log.txt`.

    La justificación que se usaba —"the financially safer interpretation when the
    conflict cannot be resolved"— pertenece a OTRO dominio: es la cuarta regla para
    resolver conflictos entre REGISTROS financieros, no una séptima regla del ranking
    de planes. Aplicarla aquí era leer el contrato donde no dice.
    """
    seguros = tuple(p for p in planes if p.brecha <= 0)
    return min(seguros, key=clave(req)) if seguros else None


def traza(planes: tuple[Plan, ...], req: Request, elegido: Plan | None) -> str:
    k = clave(req)
    filas = []
    for p in sorted(planes, key=k):
        marca = "*" if p is elegido else " "
        filas.append(f"{marca}{p.method}[{p.option_id or '-'}] "
                     f"{'piso=ROMPE:' + str(p.brecha) if p.brecha else 'piso=ok'} "
                     f"total={p.total_paid} "
                     f"inicio={p.first_day} pagos={len(p.payments)} "
                     f"cambios={len(p.changes)} "
                     f"a_tiempo={'si' if p.last_day <= req.desired_completion_date else 'NO'}")
    return " | ".join(filas)
