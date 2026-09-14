#!/usr/bin/env python3
"""D1 no es código muerto: el piso de 90 días CAMBIA la decisión cuando debe.

    python3 code/evaluation/fixtures/test_piso.py        # salida legible, exit != 0 si falla
    python3 code/evaluation/fixtures/test_piso.py -v     # además, la traza del ranking

Las 250 filas del dataset oficial no se mueven con D1 activado. Eso sólo demuestra que el
corpus no contiene el caso; NO demuestra que la regla funcione. Una compuerta que nunca se
ha visto disparar es una hipótesis sin probar. Aquí se fabrican los escenarios que el
corpus no trae y se comprueba el disparo.

    CASO A — hay un plan limpio:  el barato ROMPE el piso, el caro lo RESPETA
                                   -> tiene que ganar el caro
    CASO B — ninguno es limpio:    déficit 500 (caro) contra déficit 2000 (barato)
                                   -> tiene que ganar el de déficit 500
    CONTROL NEGATIVO — con PISO="off" ganan los baratos en los dos casos
                                   -> lo que decide es la REGLA, no otra cosa

En los dos casos el plan que debe ganar es el MÁS CARO. Es deliberado: si ganara el barato
el criterio 3 del enunciado (minimizar el total pagado) bastaría para explicar el resultado
y la prueba no probaría nada. Aquí el piso tiene que vencer al costo para pasar.

NADA aquí toca dataset/ ni ningún módulo del motor. Los objetos se construyen a mano según
contracts.py; el único estado de terceros que se manipula es `decision.candidates.PISO`,
en memoria y dentro de un try/finally, y al final se comprueba que el archivo en disco
sigue diciendo "cuotas".
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE_ROOT = HERE.parent.parent                    # code/
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from contracts import (CashPoint, Dataset, FinanceView, Forecast,  # noqa: E402
                       PaymentOption, Profile, Request)
from decision import candidates, series                            # noqa: E402
from decision.candidates import construir                          # noqa: E402
from decision.engine import decide                                 # noqa: E402
from decision.ranking import traza                                 # noqa: E402

# Fixture hermético: `decision.series.build_series` carga el dataset real de forma perezosa
# para buscar el histórico del usuario. Con la caché ya sembrada vacía no lo abre: estos
# usuarios no existen en ninguna parte y el escenario no debe depender de que dataset/ esté.
series._HIST_CACHE = {}

D = lambda x: Decimal(str(x))                                       # noqa: E731
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv

MINIMO = D(1000)
INICIO = date(2025, 1, 1)
DEADLINE = date(2025, 7, 1)

# Una sola curva para los dos casos, y es lo que los hace legibles:
#   5.000 el 1-ene · cae a 1.500 el 10-ene (el hueco) · vuelve a 4.500 el 1-feb
# Un plan que cobra DENTRO del hueco hunde el saldo; uno que espera a la recuperación, no.
CURVA = ((INICIO, D(5000)), (date(2025, 1, 10), D(1500)), (date(2025, 2, 1), D(4500)))


# ─────────────────────────────────────────────────────────────────────────────
# Constructores a mano — contracts.py, campo por campo
# ─────────────────────────────────────────────────────────────────────────────

def perfil(uid: str) -> Profile:
    """Sólo acepta plazos. Así el único candidato posible es la familia `installments`,
    que es donde PISO="cuotas" aplica el piso: la prueba mide D1, no otra cosa."""
    return Profile(
        user_id=uid, home_currency="EUR",
        current_available_balance=D(5000), minimum_balance_to_keep=MINIMO,
        financial_priorities=(), protect=(), willing_reduce=(), willing_stop=(),
        methods_considered=("installments",), max_installment_months=6,
    )


def solicitud(rid: str, uid: str, monto) -> Request:
    return Request(
        request_id=rid, user_id=uid, request_date=INICIO, request_type="purchase",
        requested_amount=D(monto), desired_completion_date=DEADLINE,
        allows_partial_payment=False, request_text="fixture sintético",
    )


def vista(prof: Profile, safe) -> FinanceView:
    pts = tuple(CashPoint(d, b) for d, b in CURVA)
    lo = min(pts, key=lambda c: (c.balance, c.day))
    fc = Forecast(user_id=prof.user_id, start=INICIO, points=pts,
                  min_balance=lo.balance, min_balance_day=lo.day)
    return FinanceView(profile=prof, forecast=fc, amount_safe_today=D(safe),
                       earliest_full_payment=None, flexible_events=(),
                       confidence=1.0, notes=("fixture",))


def opcion(oid: str, rid: str, cuota, n: int, primero: date, freq: int,
           total) -> PaymentOption:
    return PaymentOption(
        payment_option_id=oid, request_id=rid, payment_method="installments",
        payment_amount=D(cuota), number_of_payments=n, first_payment_date=primero,
        payment_frequency_days=freq, financing_fee=D(total) - D(cuota) * n,
        total_payable_amount=D(total),
    )


def dataset(prof: Profile, req: Request, opts: tuple[PaymentOption, ...]) -> Dataset:
    """Se construye completo aunque `decide` no lo reciba: es el mismo objeto que
    main.py usa para sacar `options_by_request`, y así el fixture recorre ese camino."""
    return Dataset(profiles={prof.user_id: prof}, events_by_user={prof.user_id: ()},
                   requests=(req,), options_by_request={req.request_id: opts},
                   messages_by_user={}, images=(), rates={})


# ─────────────────────────────────────────────────────────────────────────────
# Escenarios
# ─────────────────────────────────────────────────────────────────────────────

def escenario_a():
    """Dos plazos elegibles: el barato cobra dentro del hueco, el caro espera."""
    prof = perfil("user_pisoA")
    req = solicitud("req_pisoA", "user_pisoA", 2400)
    v = vista(prof, 1500)                       # capacidad 1500: los dos caben
    opts = (
        # BARATO y ROMPE: 2 × 1250 el 15-ene y el 15-feb. El 15-ene el saldo es 1500.
        opcion("payment_option_A1", req.request_id, 1250, 2, date(2025, 1, 15), 31, 2500),
        # CARO y LIMPIO: 2 × 1400 el 5-feb y el 8-mar, ya recuperado el saldo.
        opcion("payment_option_A2", req.request_id, 1400, 2, date(2025, 2, 5), 31, 2800),
    )
    return prof, req, v, dataset(prof, req, opts)


def escenario_b():
    """Dos plazos elegibles y NINGUNO limpio: 500 de déficit contra 2000."""
    prof = perfil("user_pisoB")
    req = solicitud("req_pisoB", "user_pisoB", 3200)
    v = vista(prof, 1300)                       # capacidad 1300: los dos caben
    opts = (
        # MENOS DAÑINO y CARO: 3 × 1000; sólo la primera cuota cae en el hueco.
        opcion("payment_option_B1", req.request_id, 1000, 3, date(2025, 1, 20), 31, 3000),
        # MÁS DAÑINO y BARATO: 2 × 1250 en quince días, las dos dentro del hueco.
        opcion("payment_option_B2", req.request_id, 1250, 2, date(2025, 1, 15), 14, 2500),
    )
    return prof, req, v, dataset(prof, req, opts)


# ─────────────────────────────────────────────────────────────────────────────
# Motor de comprobación
# ─────────────────────────────────────────────────────────────────────────────

class Caso:
    def __init__(self, nombre: str):
        self.nombre, self.fallos, self.lineas = nombre, [], []

    def check(self, etiqueta: str, obtenido, esperado):
        ok = obtenido == esperado
        marca = "ok  " if ok else "FALLA"
        self.lineas.append(f"    [{marca}] {etiqueta}: obtenido={obtenido!r} "
                           f"esperado={esperado!r}")
        if not ok:
            self.fallos.append(f"{self.nombre} · {etiqueta}: "
                               f"obtenido={obtenido!r} esperado={esperado!r}")
        return ok

    def nota(self, txt: str):
        self.lineas.append(f"    {txt}")

    def imprimir(self):
        estado = "VERDE" if not self.fallos else "ROJO"
        print(f"  {self.nombre} — {estado}")
        for ln in self.lineas:
            print(ln)


def planes_por_id(v, req, ds):
    opts = ds.options_by_request[req.request_id]
    return {p.option_id: p for p in construir(v, req, opts)}, opts


def correr(nombre: str, esc, ganador_esperado: str, brechas_esperadas: dict):
    c = Caso(nombre)
    prof, req, v, ds = esc()
    planes, opts = planes_por_id(v, req, ds)

    # Precondición: los dos planes TIENEN que ser elegibles. Si uno no compite, el
    # resultado no prueba nada sobre el piso — probaría el filtro de elegibilidad.
    for oid in brechas_esperadas:
        c.check(f"{oid} es candidato elegible", oid in planes, True)
    if len(planes) != len(brechas_esperadas):
        c.nota(f"!! candidatos construidos = {sorted(planes)}")
        c.fallos.append(f"{nombre}: se esperaban {len(brechas_esperadas)} candidatos")
        return c

    for oid, esperada in brechas_esperadas.items():
        c.check(f"{oid} brecha (cuánto hunde el saldo bajo el mínimo)",
                planes[oid].brecha, D(esperada))

    if ganador_esperado is None:
        # Ningún candidato es seguro. El enunciado: "`not_recommended` is the fallback
        # when no safe eligible payment is available. WHEN MORE THAN ONE ELIGIBLE PLAN
        # IS SAFE, rank the plans in this order". El ranking opera SÓLO sobre planes
        # seguros: si ninguno lo es, no hay recomendación que dar.
        c.check("los dos candidatos rompen el piso",
                all(planes[o].brecha > 0 for o in brechas_esperadas), True)
        d = decide(v, req, opts)
        c.check("opción elegida (ninguna)", d.chosen_option_id, None)
        c.check("método", d.recommended_payment_method, "not_recommended")
        c.check("sin plan de pagos", len(d.payments), 0)
        c.nota("ningún plan seguro -> not_recommended, no 'el que menos rompe'")
        return c

    # El que debe ganar tiene que ser el MÁS CARO: si no, ganaría por el criterio 3.
    ganador, perdedor = ganador_esperado, next(o for o in brechas_esperadas
                                               if o != ganador_esperado)
    c.check(f"{ganador} cuesta MÁS que {perdedor} (si no, ganaría por el criterio 3)",
            planes[ganador].total_paid > planes[perdedor].total_paid, True)

    d = decide(v, req, opts)
    c.check("opción elegida", d.chosen_option_id, ganador_esperado)
    c.check("método", d.recommended_payment_method, "installments")
    c.nota(f"plan: " + " | ".join(f"{p.day}:{p.amount}" for p in d.payments))
    if VERBOSE:
        c.nota("traza: " + traza(tuple(planes.values()), req, planes.get(ganador)))
    return c


def control_negativo():
    """Con el piso apagado ganan los baratos. Prueba que decide la REGLA, no la curva."""
    c = Caso("CONTROL NEGATIVO · PISO='off'")
    previo = candidates.PISO
    try:
        candidates.PISO = "off"
        c.check("PISO en memoria", candidates.PISO, "off")
        for nombre, esc, barato in (("caso A", escenario_a, "payment_option_A1"),
                                    ("caso B", escenario_b, "payment_option_B2")):
            prof, req, v, ds = esc()
            opts = ds.options_by_request[req.request_id]
            planes, _ = planes_por_id(v, req, ds)
            c.check(f"{nombre}: con el piso apagado ninguna brecha sobrevive",
                    sorted({str(p.brecha) for p in planes.values()}), ["0"])
            d = decide(v, req, opts)
            c.check(f"{nombre}: gana el barato que rompe", d.chosen_option_id, barato)
    finally:
        candidates.PISO = previo
    c.check("PISO restaurado en memoria", candidates.PISO, previo)

    # Y el archivo en disco nunca se tocó: el motor sigue congelado.
    fuente = (CODE_ROOT / "decision" / "candidates.py").read_text(encoding="utf-8")
    c.check("decision/candidates.py en disco sigue en 'filtro'",
            'PISO = "filtro"' in fuente, True)
    return c


def main() -> int:
    print("=" * 78)
    print("D1 · EL PISO DE 90 DÍAS CAMBIA LA DECISIÓN — fixtures sintéticos")
    print(f"PISO al arrancar = {candidates.PISO!r} · mínimo a conservar = {MINIMO} EUR")
    print("curva: " + " → ".join(f"{d} {b}" for d, b in CURVA))
    print("=" * 78)

    casos = [
        correr("CASO A · existe un plan limpio → gana el limpio aunque cueste más",
               escenario_a, "payment_option_A2",
               {"payment_option_A1": 750, "payment_option_A2": 0}),
        correr("CASO B · ninguno es limpio → NO se recomienda ninguno",
               escenario_b, None,
               {"payment_option_B1": 500, "payment_option_B2": 2000}),
        control_negativo(),
    ]
    print()
    for c in casos:
        c.imprimir()
        print()

    fallos = [f for c in casos for f in c.fallos]
    print("=" * 78)
    if fallos:
        print(f"ROJO · {len(fallos)} comprobaciones falladas")
        for f in fallos:
            print(f"  !! {f}")
        print("=" * 78)
        return 1
    total = sum(len([ln for ln in c.lineas if "[ok  ]" in ln or "[FALLA]" in ln])
                for c in casos)
    print(f"VERDE · {total}/{total} comprobaciones · D1 dispara en A, ordena en B, "
          f"y devuelve not_recommended cuando ninguno es seguro")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
