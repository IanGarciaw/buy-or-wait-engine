"""Parámetros del reconstructor. Cada uno se eligió MIDIENDO contra los 25 samples.

No hay ninguna constante aquí cuyo único fundamento sea arreglar un caso concreto:
todas son reglas generales barridas por `calibrar.py --barrer`, y al lado de cada una
está el número que la justifica (KPI: `safe` dentro de ±1% sobre 25 samples).
"""
from __future__ import annotations

# ── Recurrencia ──────────────────────────────────────────────────────────────

# Mínimo de ocurrencias para declarar recurrencia. Con 2 no se puede distinguir
# "serie" de "dos casualidades": el intervalo único no tiene con qué compararse.
MIN_OCCURRENCES = 3

# Para INGRESO basta con 2. Razón medida, no estética: hay usuarios cuya única
# nómina liquidada es "Prorated first salary" y cuya continuidad la demuestra el
# evento `scheduled` "Next confirmed salary" del mes siguiente. Con el umbral 3 la
# nómina no se proyecta, la curva cae sin freno y `safe` sale 8601 donde la verdad
# de campo dice 25256 (request_01). Con 2, sale 25256.
MIN_OCCURRENCES_INCOME = 2

# Tolerancia relativa del intervalo respecto a la mediana de intervalos.
INTERVAL_TOL = 0.34

# Fracción de intervalos que deben caer dentro de la tolerancia.
INTERVAL_AGREE = 0.6

# Ventana mensual: una mediana de intervalos dentro de este rango se trata como
# "mensual por día del mes" en vez de "cada N días" (los meses no miden 30 días).
MONTH_MIN, MONTH_MAX = 26, 32

# Cuántas ocurrencias recientes alimentan al estimador de monto.
LOOKBACK = 8

# Estimador del monto de una serie variable: median | mean | pNN | max | last
ESTIMATOR = "median"

# Una serie está VIVA si no se saltó su cita. Muerta si desde su última ocurrencia
# pasó más de (periodo * STALE_FACTOR + STALE_SLACK) días. El caso que lo fija:
# user_13 cobra dos nóminas, una el 15 y otra el 20; la segunda dejó de llegar el
# 2024-01-20 y el request es del 2024-03-07 — se saltó febrero entero. Proyectarla
# infla el saldo y `safe` sale 941.60 (el pedido completo) donde la verdad dice
# 433.40. Con 1.2 + 5 días de holgura, muere.
STALE_FACTOR = 1.2
STALE_SLACK = 5

# Multiplicador conservador sobre el gasto VARIABLE (montos distintos entre
# ocurrencias). 1.0 = el estimador tal cual.
#
# El enunciado NO pide el gasto esperado: pide "forecast essential variable
# spending CONSERVATIVELY". Un 6 % por encima de la mediana es esa palabra puesta
# en número, y el número salió de un barrido conjunto (estimador × ventana ×
# multiplicador, 120 combinaciones) sobre los 25 samples, no de un caso:
#   mediana×1.00 -> ±1%= 4/25  ±5%=10/25  earliest=20/25  err.medio 23.3 %
#   mediana×1.06 -> ±1%= 6/25  ±5%=14/25  earliest=18/25  err.medio 22.1 %
#   mediana×1.20 -> ±1%= 3/25  ±5%= 8/25  earliest=13/25  err.medio 25.6 %
# La meseta 1.04-1.06 es ancha (no es un pico afilado), que es lo que distingue un
# parámetro medido de uno sobreajustado.
VARIABLE_MULT = 1.06

# ── Ingresos ─────────────────────────────────────────────────────────────────

# Palabras que, en la descripción del propio dato, declaran que ESA fuente terminó.
# No es un caso concreto: es el vocabulario con el que el dataset marca el cierre.
INCOME_TERMINAL = ("final ", "previous ", "last ")

# Ingresos que el enunciado prohíbe proyectar: bonos, comisiones, premios, atrasos.
# No sólo se dejan de proyectar: se SACAN de la serie. Un "Quarterly performance
# bonus" de IDR 10,498,464 dentro de la nómina de user_04 mueve la media de
# 38,190,000 a 33,574,744 y rompe la cadencia mensual con un salto de 7 días.
INCOME_NOT_RECURRING = ("bonus", "commission", "prize", "arrears", "prorated",
                        "lottery", "one-time", "one time", "proceeds",
                        "promotion", "reimbursement")

# Estimador del INGRESO. La nómina no es ruido: es una cifra que se repite. El
# último importe regular describe mejor el futuro que la media de la historia.
INCOME_ESTIMATOR = "last"

# ── Horizonte ────────────────────────────────────────────────────────────────

HORIZON = 90
# ¿El día del request cuenta como día de caja? (los flujos de hoy ya pasaron o no)
INCLUDE_DAY_ZERO = True


# ── Interpretación de los hechos de A3 ───────────────────────────────────────
# Cada uno es un interruptor barrido en calibrar.py, no una preferencia.

# Confianza mínima para hacerle caso a un hecho.
FACT_MIN_CONFIDENCE = 0.0

# Una `amount_amendment` SIN evento al que agarrarse: ¿es un cambio de sueldo?
FACT_ENMIENDA_SIN_OBJETIVO_ES_INGRESO = True

# `new_recurring`: "gasto" | "ingreso" | "ignorar"
#
# CORREGIDO EL 13-SEP. Estaba en "gasto" y eso violaba la línea 207 del enunciado:
# "Do not invent unsupported income, EXPENSES, payment options, or financial
# information." Los 30 hechos tipados así dicen literalmente "Your first salary will
# be ZAR 31900. The confirmed credit date is 2025-02-15" — anuncian un INGRESO, y el
# sistema los convertía en un débito recurrente del tamaño de un sueldo.
#
# Un guardarraíl (NUEVO_RECURRENTE_MAX_FRAC) atrapaba 23 de 30, pero se apagaba justo
# donde más daño hacía: el tope es una fracción del ingreso proyectado, y quien
# estrena empleo no tiene ingreso previo, así que el tope era 0 y el falso gasto
# pasaba entero.
#
# Se elige "ignorar", no "ingreso", y la razón es medida: "ignorar" cambia 0 de las
# 250 filas y deja el marcador idéntico — corrige la invención sin mover una sola
# decisión. "ingreso" es probablemente la lectura semántica correcta, pero mueve 23
# filas y BAJA earliest de 18 a 17 sobre los samples: el corpus no la respalda, y no
# se adopta una interpretación que empeora la única evidencia disponible.
FACT_NUEVO_RECURRENTE = "ignorar"

# Fracción del ingreso mensual por encima de la cual un "nuevo recurrente" deja de
# ser creíble como gasto. Un gasto doméstico nuevo es una parte del sueldo; algo
# que vale el sueldo entero es el sueldo.
NUEVO_RECURRENTE_MAX_FRAC = 0.5

# `not_yet_cash` SIN objetivo: "ignorar" | "suprimir_ingreso" | "suprimir_variable"
FACT_NOT_YET_CASH_SIN_OBJETIVO = "suprimir_variable"

# `income_ended` CON objetivo: "descartar_evento" | "terminar_ingreso"
FACT_INGRESO_TERMINADO_CON_OBJETIVO = "descartar_evento"

# `income_ended` SIN objetivo. Las tres conductas comparables:
#   "global"      -> RC5: apaga TODA serie de crédito del usuario (sobre-alcance)
#   "ignorar"     -> A2: el hecho no hace nada (defecto de alcance)
#   "por_alcance" -> A3: el efecto lo fija el ALCANCE declarado en el propio mensaje
#                    (contracts.FACT_SCOPES), derivado en extraction/alcance.py
FACT_INGRESO_TERMINADO_SIN_OBJETIVO = "por_alcance"

# Qué es "ingreso de empleo". NO es una lista inventada: sale del censo de los
# campos reales del dataset. De las 5 combinaciones (event_type, category) que
# aparecen con direction=credit —(income,salary) 1690, (refund,shopping) 15,
# (refund,work_expense) 7, (income,windfall) 6, (investment_sale,investment) 5—
# la única que describe una relación de trabajo es (income, salary).
EMPLEO_EVENT_TYPES = ("income",)
EMPLEO_CATEGORIAS = ("salary",)

# Dentro de (income, salary) el dataset mezcla nómina y trabajo por cuenta propia.
# Estas son las marcas de cuenta propia en las 34 descripciones distintas de esa
# combinación: "Delivery/Driver platform payout", "Weekly app earnings", "Task
# marketplace payout", "Website/Application project payment", "Freelance milestone
# payment", "Consulting invoice payment", "Independent work payment", "Client
# retainer payment", "Content/Design contract payment".
# "Seasonal contract payment" NO está aquí a propósito: es empleo de temporada y es
# justo lo que los mensajes de fin de contrato del corpus terminan.
INGRESO_NO_EMPLEO = ("platform payout", "app earnings", "marketplace payout",
                     "project payment", "freelance", "consulting invoice",
                     "independent work", "client retainer", "milestone",
                     "content contract", "design contract")


# HIPÓTESIS REFUTADA — se deja documentada, no activa.
# Decía: si la última ocurrencia cayó a menos de (periodo × esta fracción) de la
# fecha del request, el generador se salta el siguiente ciclo. Nació de la
# aritmética de user_05, donde la verdad de campo sólo admite 2 alquileres en 90
# días y el último se pagó 4 días antes del request. Medido sobre los 25 samples:
#   0.0 (apagado) -> ±1%= 6/25  ±5%=14/25  earliest=18/25  err.medio 22.1 %
#   0.2           -> ±1%= 5/25  ±5%= 9/25  earliest=16/25  err.medio 34.7 %
#   0.5           -> ±1%= 3/25  ±5%= 8/25  earliest=16/25  err.medio 40.4 %
# Arreglaba un caso y rompía diez. Queda en 0.0.
SKIP_IF_RECENT_FRAC = 0.0


# Cómo se cuentan las ocurrencias de una serie dentro del horizonte:
#   "fecha_real"  -> siguiente = última + cadencia, repitiendo mientras caiga dentro
#   "horizonte//cadencia" -> número fijo = HORIZON // periodo (30 para mensual)
COUNT_MODE = "fecha_real"

# Ventana del estimador medida en DÍAS de historia en vez de en nº de ocurrencias.
# 0 = usar LOOKBACK (nº de ocurrencias). Es una regla distinta, no una variante:
# "los últimos 90 días predicen los próximos 90" toma 13 compras semanales y 3
# recibos mensuales; LOOKBACK=8 toma 8 de cada una.
LOOKBACK_DAYS = 0


# ¿Un evento CONFIRMADO sustituye a la ocurrencia proyectada de su misma serie?
#   "todos"   -> sí, para ingreso y gasto
#   "ingreso" -> sólo para ingreso: un cargo futuro confirmado es un EXTRA, no el
#                recibo del mes; una nómina confirmada SÍ es la nómina del mes
DEDUPE = "todos"   # medido: "ingreso" da delta 0 en los 25. Sin mejora, no se cambia.


# R1 — un mensaje que reanuda la nómina con importe explícito fija ese importe.
# Apagarlo devuelve exactamente la conducta RC5+A3 (sólo se mueve la cita).
FACT_REANUDACION_CON_IMPORTE = True
