# DIAGNÓSTICO FORENSE · `amount_safe_to_pay`

Agente 6 (forense). Todo lo de abajo está medido con los scripts de esta carpeta contra
`dataset/sample_requests.csv`. Ninguna afirmación va sin su cifra.

**Instrumento**: `analysis/lab.py` reimplementa el forecast de forma independiente de
`finance/` porque A1 estaba editando `finance/params.py` en vivo (ESTIMATOR pasó de
`mean` a `median`, LOOKBACK de 6 a 8 y apareció VARIABLE_MULT=1.06 entre dos corridas
mías). La línea base del lab —`mean`/lookback 6/mult 1.0— reproduce exactamente el
baseline: **safe ±1% = 4/25, earliest = 18/25**. Los conteos ayuda/perjudica/igual de
abajo están medidos sobre ESE baseline con `analysis/sweep.py`.

---

## RESUMEN — reglas ordenadas por impacto

| # | Regla | Samples | Requests de evaluación |
|---|---|---|---|
| 1 | **`new_recurring` NO EXISTE en este corpus.** Los 30 hechos así tipados son anuncios de nómina ("Your first salary will be EUR 1661"). Cobrarlos como gasto carga al usuario un recibo mensual igual a su sueldo entero. | +1 (90.6%→…, evita 100%) | **29 / 250** |
| 2 | **`not_yet_cash` sin objetivo suprime SÓLO el ingreso cuyo importe varía.** Matar todo el ingreso rompe 2 samples; ignorarlo rompe 1 con error 2000%. | +1 (evita 2000%) | 44 / 250 |
| 3 | **`income_ended` que dice "the remaining confirmed monthly salary is X" mata UNA fuente, no el ingreso.** Hoy `ingreso_terminado=True` borra también el sueldo que el propio mensaje confirma. | 0 (ningún sample) | **6 / 250** |
| 4 | **`not_yet_cash` que confirma importe y fecha ("client approved an invoice payment of X, settlement expected on D") es un ingreso CONFIRMADO, no una supresión.** Hoy se tira el dato y además se suprime la serie. | 0 (ningún sample) | **18 / 250** |
| 5 | **Un mensaje puede traer DOS hechos.** `MessageFact` es uno-por-mensaje: en 8 mensajes que anuncian sueldo + gasto recurrente nuevo, el gasto se pierde siempre. | 0 medible | 8 / 250 |
| 6 | **Sin regla.** El resto del error (05, 10, 13, 15, 25 y 12 más) es un sesgo de ±20% en el gasto proyectado del PRIMER ciclo. Ver §7: qué queda falsado y qué no. | — | — |

Reglas 1–4 valen poco en los 25 samples y mucho en los 250: el muestreo de samples
tiene 1 caso de la clase 1 y ninguno de las clases 3 y 4.

---

## Aritmética inversa — los 25, no sólo los 6

`min_objetivo = safe_esperado + minimum_balance_to_keep`. Cuando `safe == requested`
sólo es cota inferior (marcado CAP).

| req | user | esperado | obtenido | min nuestro | min objetivo | Δ | día del mínimo |
|---|---|---|---|---|---|---|---|
| 05 | user_05 | 737.00 | **0.00** | 8 454.22 | 13 837.00 | −5 382.78 | +90 (fin) |
| 10 | user_10 | 12 700.00 | **0.00** | 194 445.38 | 238 100.00 | −43 654.62 | +90 (fin) |
| 13 | user_13 | 433.40 | 549.39 | 1 849.39 | 1 733.40 | +115.99 | +68 |
| 14 | user_14 | 597.74 | 609.59 | 2 809.59 | 2 797.74 | +11.85 | +10 |
| 15 | user_15 | 83.05 | 7.84 | 1 207.84 | 1 283.05 | −75.21 | +8 |
| 25 | user_25 | 1 425 000.00 | 356 456.81 | 23 735 556.81 | 24 804 100.00 | −1 068 543.19 | +8 |

**Aviso al Director**: tres de las seis cifras del encargo eran de una corrida anterior.
Hoy, con el código tal como está: request_14 da 609.59 (no 0), request_15 da 7.84 (no 0),
request_10 da 0.00 (no 266 700) y request_13 da 549.39 (no 941.60). El defecto que
producía 941.60 —la segunda nómina muerta de user_13— **ya está arreglado** por la regla
de caducidad (`periodo × 1.2 + 5 días`) en `finance/params.py`.

---

## 1 · request_05 · user_05 — SIN CAUSA ENCONTRADA

**Aritmética inversa.** balance 46 475.10 · mínimo 13 100 · esperado 737.00
→ `min_90d` debe ser **13 837.00** → salida neta en 90 días **exactamente 32 638.10**.

**La curva.** El usuario no tiene NINGÚN evento con fecha ≥ 2025-11-06. Su último ingreso
es `event_390` "**Final** employer payroll" (2025-10-15, ZAR 14 740): la palabra la mata
correctamente. Sin ingresos la curva es monótona decreciente y el mínimo cae
forzosamente el día 90 (2026-02-04, saldo 8 454.22). Reproducible:
`python3 analysis/curva2.py request_05`.

Descomposición nuestra (`analysis/descomponer.py request_05`), total **−38 020.88**:

```
rent           3 × 4 972.00 = −14 916.00      utilities      3 ×   686.71 = −2 060.12
groceries     13 ×   709.57 =  −9 224.41      shopping       3 ×   397.85 = −1 193.55
debt_repay     3 ×   968.00 =  −2 904.00      cloud_storage  3 ×   113.30 =   −339.90
transport      7 ×   394.95 =  −2 764.67      healthcare     3 ×   699.01 = −2 097.02
family_support 3 ×   840.40 =  −2 521.20
```

**Lo falsado (no lo repitan).**
1. *Quitar una serie entera*: ningún subconjunto suma 5 382.78. Los más cercanos son
   5 304.03 (utilities+debt+cloud) y 5 425.20 (family+debt). No hay corte limpio.
2. *Modelo de agregado mensual*: el gasto real de user_05 es 12 771.77 (ago), 12 822.73
   (sep), 12 799.64 (oct). Media × 3 = 38 394.14; media de los 6 meses × 3 = 33 408.29.
   El objetivo 32 638.10 equivale a **2.55 meses** de su gasto real. Ningún múltiplo.
3. *Barrido exhaustivo estimador × conteo* (`analysis/buscar.py request_05`): 12
   estimadores × 512 combinaciones de conteo (n o n−1 por serie). El mejor ajuste
   (diff 1.20) exige `rent:3, family:2, shopping:2, groceries:12, transport:7` — un
   patrón sin regla detrás. Con 512 máscaras, un diff < 5 sobre 32 638 es ruido, no
   evidencia.
4. *Horizonte*: barrido 75–95 días. Mejora, pero es una **meseta de 12 días idénticos**
   (75 a 86 dan exactamente ±1%=5, medERR 5.78%, earl=21). Una regla de calendario real
   daría un pico en un valor, no una meseta. Es sobreajuste, no descubrimiento.

**Regla general propuesta.** Ninguna. Lo que sí queda establecido: la verdad de campo
cobra el **85.8 %** de nuestro gasto a 90 días (`k = 0.8584`, ajuste inverso en
`analysis/inverso.py`). Coincide con el 85.0 % de su propio gasto histórico reciente.
Es un sesgo, no un evento.

---

## 2 · request_10 · user_10 — LA SUPRESIÓN DE INGRESO ES CORRECTA

**Aritmética inversa.** balance 750 155 · mínimo 225 400 · esperado 12 700
→ `min_90d` = **238 100** → salida neta a 90 días **512 055.00**.

**El hallazgo que vale.** 512 055 sobre un balance de 750 155 sólo es alcanzable **con
cero ingreso en los 90 días**. El pago semanal de QuickCrew que detectamos vale
52 239.80 × 13 semanas ≈ 679 117: con él, el mínimo no bajaría de ~700 000 y `safe`
saldría en el tope (266 700, que es exactamente lo que el sistema devolvía antes).
**La verdad de campo también lo suprime.** `message_07`:

> "The next QuickCrew payout is still pending. The weekly earnings shown in the QuickCrew
> app can change until the payout is closed. The balance isn't withdrawable until the
> payout shows as completed."

**Regla general (confirmada).** Un hecho `not_yet_cash` sin `target_event_id` suprime la
proyección de la serie de ingreso **cuyo importe varía entre ocurrencias**; la nómina de
importe fijo sigue. Verificación cruzada sobre los otros 19:

| variante | ayuda | perjudica | igual |
|---|---|---|---|
| `suprimir_variable` (actual) | — | — | baseline |
| `ignorar` | 0 | **1** (request_10: 100% → 2000%) | 24 |
| `suprimir_ingreso` (matar todo) | 0 | **2** (request_04 25.5%→100%, request_23 9.7%→100%) | 23 |

**Lo que queda.** Nuestra salida es 555 709.62 contra 512 055 → `k = 0.9214`. Mismo sesgo
que request_05: el resto es gasto sobreproyectado, no un evento perdido.

---

## 3 · request_15 · user_15 — NÓMINA COBRADA COMO GASTO (la regla #1)

**Aritmética inversa.** balance 1 770.05 · mínimo 1 200 · esperado 83.05
→ `min_90d` = **1 283.05**. El mínimo cae el día +8 (2026-01-14), la víspera de la
nómina del 15. Salida neta hasta ahí: objetivo **487.00**, nuestra **562.21**.

**La curva hasta el valle** (`analysis/curva2.py request_15`):

```
2026-01-06  −57.17  groceries    2026-01-13  −84.00  debt_repayment
2026-01-07  −33.39  transport    2026-01-13  −57.17  groceries
2026-01-08  −86.39  utilities    2026-01-13  −11.00  music_subscription
2026-01-10  −40.70  dining       2026-01-14  −33.39  transport   <<< MÍNIMO 1 207.84
2026-01-10 −159.00  education    2026-01-15 +1661.00 salary
```

**El diagnóstico.** `message_11` dice:

> "A quick update from the payroll team at Riverline Retail. **Your first salary will be
> EUR 1661. The confirmed credit date is 2026-01-15.**"

El extractor lo tipa `kind="new_recurring"`, y `FACT_NUEVO_RECURRENTE="gasto"` lo mete
como un **gasto recurrente mensual de EUR 1 661** — el sueldo entero del usuario cobrado
como recibo. Con esa lectura `safe` baja a 0 (error 100 %).

**Y es general, no un caso.** Revisé los 30 hechos `new_recurring` del corpus contra el
texto de su mensaje: **los 30 son anuncios de nómina** ("Your first salary will be…",
"Gaji pertama Anda sebesar…", "Your confirmed base salary is…"). **Cero** son un gasto
recurrente nuevo. El importe es ≥ 50 % de la nómina máxima del usuario en los 30 casos
(ratio 1.00 en 23 de ellos, 1.67–1.82 en los 7 restantes porque el sueldo sube).

**Regla general propuesta.**
> Un hecho `new_recurring` cuyo importe alcanza o supera la mitad de la mayor nómina
> histórica del usuario es un **ingreso**, no un gasto. Mejor aún: en origen, un mensaje
> de `source_type=employer` que anuncia "first salary / confirmed base salary / gaji
> pertama" con importe y fecha debe tiparse `income_change`, no `new_recurring`.

**Verificación cruzada (25 samples, `analysis/sweep.py`):**

| variante | ayuda | perjudica | igual |
|---|---|---|---|
| guarda ≥50% ⇒ ingreso (o `ingreso` siempre) | **1** | 0 | 24 |
| `gasto` sin guarda | 0 | **1** (request_15 90.6% → 100.0%) | 24 |
| `ignorar` | 0 | 0 | 25 |

Sólo 1 de los 25 samples tiene un `new_recurring`. En el conjunto de evaluación son
**29 de 250 requests**. Ésa es la cifra que importa.

**Residual tras la guarda:** −75.21 en el valle. Misma clase que 05/10/25.

---

## 4 · request_25 · user_25 — VALLE TEMPRANO, SIN CAUSA ENCONTRADA

**Aritmética inversa.** balance 32 063 050 · mínimo 23 379 100 · esperado 1 425 000
→ `min_90d` = **24 804 100**. Valle el día +8 (2024-03-14), víspera de la nómina.
Salida neta hasta el valle: objetivo **7 258 950**, nuestra **8 327 493.19** → −1 068 543.19.

**La curva hasta el valle** (sólo 10 flujos):

```
03-06 −1 036 673.14 dining        03-12   −126 350.00 cloud_storage
03-06 −1 323 654.72 utilities     03-12 −1 059 028.74 shopping
03-07   −904 400.00 insurance     03-13 −1 036 673.14 dining
03-09 −1 235 506.35 groceries     03-14   −459 592.42 entertainment  <<< MÍN 23 735 556.81
03-09   −573 800.00 streaming     03-15 +28 499 994.00 salary (USD 1 800 @ 15 833.33)
03-10   −571 814.68 transport
```

**Calendario verificado, no asumido.** Contrasté cada ancla contra la historia real:
dining cae miércoles exactos (01-24, 01-31, 02-07, 02-14, 02-21, 02-28 → 03-06 ✔),
transport cada 5 días exactos (01-20…03-05 ✔), groceries cada 10 (01-29, 02-08, 02-18,
02-28 → 03-09 ✔), utilities día 6 ✔. **El calendario está bien; el problema son los
importes o la composición.**

**Lo falsado.**
1. *Moneda*: la nómina es USD 1 800 convertida con la tasa de la fecha de liquidación
   (15 833.33 IDR/USD) = 28 499 994. No es el problema — el valle es anterior a la nómina.
2. *Excluir el día 0*: pasa de −1 068 543.19 a **+1 291 784.67**. La verdad está en medio:
   ninguna frontera de día lo explica.
3. *Estimadores*: `last` empeora (+770 k), `min` es el mejor y aun así deja 14.3 % de error.
4. `event_2287` "Failed subscription debit" (IDR 1 246 400, 2024-03-04) está bien
   descartado por `status=failed`.

**Regla general propuesta.** Ninguna. `k = 0.8717`.

---

## 5 · request_13 · user_13 — YA ARREGLADO; LO QUE QUEDA ES `earliest`

**Aritmética inversa.** balance 2 789.52 · mínimo 1 300 · esperado 433.40
→ `min_90d` = **1 733.40**. El nuestro: 1 849.39 (lab) / 1 717.20 (finance con los
parámetros de A1 de las 23:07). Δ = +115.99.

**El defecto que el encargo describe ya no existe.** El 941.60 venía de proyectar la
segunda nómina del hogar ("Second household income", día 20), que dejó de llegar el
2024-01-20 y se saltó febrero entero antes del request del 2024-03-07. La regla de
caducidad (`periodo × 1.2 + 5 días`) la mata. Medido hoy: 549.39, no 941.60.

**Lo que sí sigue roto: `earliest`.** La verdad dice `2024-05-15` (= la fecha límite del
request, y el plan es `2024-05-15:941.60`). Para eso el mínimo del sufijo desde el 15 de
mayo hasta el fin del horizonte debe ser ≥ 1 300 + 941.60 = **2 241.60**. Nuestra curva
cae a 1 875.51 el 2024-06-05 (último día). Es decir: **la verdad gasta al menos 366.09
menos que nosotros en las tres últimas semanas del horizonte**. Nuestro gasto en
[05-15, 06-05] es 1 185.23 (4 transport + 3 groceries + 1 dining + 1 rent); el de la
verdad es ≤ 819.14. Misma clase de error que 05/10/25, medida por otra vía.

**Regla general propuesta.** Ninguna nueva. La de caducidad ya está y es correcta:
sobre los 25 samples no perjudica a ninguno.

---

## 6 · request_14 · user_14 — EL SEGUNDO HECHO DEL MENSAJE SE PIERDE

**Aritmética inversa.** balance 3 931.74 · mínimo 2 200 · esperado 597.74
→ `min_90d` = **2 797.74**. Nuestro 2 809.59 (Δ +11.85 = **2.0 % de error**, no es
estructural). Valle el día +10 (2025-08-14), víspera del sueldo del 15.

**La curva hasta el valle** — salida objetivo 1 134.00, nuestra 1 122.15:

```
08-07 −148.22 utilities     08-12 −350.00 debt_repayment
08-10 −111.23 groceries     08-13  −14.00 cloud_storage
08-11  −91.90 healthcare    08-13 −129.47 shopping
08-11  −51.33 transport     08-14 −226.00 family_support  <<< MÍN 2 809.59
                            08-15 +2 717.00 sueldo reanudado (mensaje)
```

**El diagnóstico estructural (aunque aquí casi no duela).** `message_10` trae **dos**
hechos:

> "**Regular salary of EUR 2717 resumes on 2025-08-15.** **A new recurring childcare
> payment begins in the same month.** The updated pay and deductions will appear from the
> next cycle."

`MessageFact` es un hecho por mensaje (`kind` es un solo campo), así que el extractor
emite `income_change(2717, 2025-08-15)` y **el gasto de guardería es inexpresable**: se
pierde en silencio. Aquí no mueve el valle porque el valle es el 08-14 y la guardería
empieza "in the same month", presumiblemente del 15 en adelante — lo cual explica por qué
el error residual es sólo 11.85 y confirma que la verdad **tampoco** lo cobra antes del 15.

**Y es general**: 8 mensajes del corpus anuncian sueldo + gasto recurrente nuevo a la vez
(`message_10, 63, 66, 91, 97, 113, 120, 170`). En los 8, el `kind` extraído es
`income_change` o `income_date_change`: **la mitad del mensaje se pierde siempre**.

**Regla general propuesta.**
> El extractor debe poder devolver una **lista** de `MessageFact` por mensaje, no uno.
> Un mensaje de nómina que además anuncia un gasto recurrente nuevo produce dos hechos.
> Mientras el contrato sea uno-por-mensaje, esos 8 usuarios van con el gasto de menos.

**Verificación cruzada.** 0 samples cambian (el único afectado, user_14, tiene el valle
antes de la fecha de efecto). 8 requests de evaluación afectados. No puedo medir el signo
contra la verdad de campo porque ninguno de los 8 está en los samples: lo reporto como
defecto de contrato demostrado por el texto, **no** como mejora medida.

---

## 7 · LO QUE EXPLICA LOS SEIS A LA VEZ (y lo que no)

**Ajuste inverso** (`analysis/inverso.py`): busco el multiplicador `k` sobre TODO el gasto
proyectado que reproduce el `min_90d` objetivo de cada sample.

```
k sobre los 21 samples no capados:  min 0.858 · MEDIANA 1.016 · max 1.341
```

Tres consecuencias:

1. **El nivel medio del modelo es correcto** (mediana 1.016). No hay un sesgo global que
   corregir con una constante: `var_mult=0.9` ayuda a 7 y perjudica a 13.
2. **El error por caso es ±20 %** y **en 20 de los 25 samples el valle cae entre el día 6
   y el 15** — la víspera de la primera nómina del horizonte. En esa ventana hay 8–11
   flujos: un error del 20 % **es un flujo**, no un estimador mal elegido.
3. Por eso ningún estimador global gana: la matriz por sample
   (`mean/median/last/p75/max/min × lookback 6/0`) es **bimodal** — 02, 03, 04, 18, 19,
   20, 23, 24 prefieren `max` (hay que gastar MÁS) y 06, 10, 12, 15, 22, 25 prefieren
   `min` (hay que gastar MENOS). No existe un estimador que sirva a los dos grupos.

**La hipótesis del día 0, falsada con un contraejemplo directo.** Excluir los flujos del
día del request ayuda a 4 y perjudica a 6. Y no es cuestión de calibración: la misma
estructura da respuestas opuestas.

| sample | flujo del día 0 | serie | hueco desde la última | ¿la verdad lo cobra? |
|---|---|---|---|---|
| request_15 | groceries −57.17 | `days:7` | **7 d exactos** | **NO** |
| request_23 | groceries −1 573.34 | `days:7` | **7 d exactos** | **SÍ** |
| request_12 | utilities −3 509.72 | `month:5` | 31 d | **NO** |
| request_18 | utilities −111.27 | `month:7` | 30 d | **SÍ** |

Series idénticas, anclas idénticas, respuestas opuestas. **No hay regla de frontera de
día.** La mejora aparente de `include_day_zero=False` es coincidencia de magnitudes.

**Resumen de lo falsado, para que nadie lo repita:**

| hipótesis | ayuda | perjudica | veredicto |
|---|---|---|---|
| no proyectar la 1ª ocurrencia mensual | 1 | **21** | catastrófica |
| tope de 2 ocurrencias mensuales futuras | 1 | 4 | no |
| horizonte 75–86 días | 2 | 1 | meseta de 12 días = sobreajuste |
| horizonte 87–95 días | 0–1 | 0 | nulo |
| excluir el día 0 | 4 | 6 | falsada con contraejemplo |
| estimador `median` | 7 | 12 | no |
| estimador `last` | 7 | 12 | no |
| estimador `min` | 7 | 13 | no |
| estimador `max` | — | — | bimodal, ver arriba |
| `lookback=0` (toda la historia) | **12** | 7 | única mejora neta de calibración |
| `var_mult=0.9` | 7 | 13 | no |
| tope de ocurrencias semanales | 0 | 0 | nulo |
| ignorar los hechos de A3 | 2 | 5 | los hechos suman |

**La única palanca de calibración con signo positivo neto es `lookback=0`** (usar toda la
historia en vez de las últimas 6–8 ocurrencias): mejora 12, empeora 7, medERR 8.92 % →
6.43 %. No arregla ninguno de los seis, pero es la mejor que encontré y no es un caso
particular.

---

## 8 · REGLAS DE EXTRACCIÓN QUE NO SE VEN EN LOS SAMPLES PERO PESAN EN LOS 250

Clasifiqué los 215 mensajes del corpus por el TEXTO, no por el `kind` extraído.

**`income_ended` sin objetivo — 19 mensajes, 3 sub-tipos con consecuencias opuestas:**

| sub-tipo | n | texto | qué hacemos | qué se debe hacer |
|---|---|---|---|---|
| A | **6** | "One household employment record has ended. **The remaining confirmed monthly salary is EUR 1628.**" | `ingreso_terminado=True` → borra TODO el ingreso | matar UNA fuente y dejar la restante en el importe que el mensaje confirma |
| B | 4 | "Your employment has ended. There are no regular salary payments scheduled after…" | borrar todo | correcto |
| C | 9 | "The current seasonal contract has ended. No off-season income or renewal has been confirmed." | borrar todo | correcto |

El sub-tipo A es exactamente la estructura de user_13 (dos nóminas del hogar, una muere),
pero anunciada por mensaje en vez de deducible por caducidad. Hoy le quitamos al usuario
un sueldo que el propio mensaje confirma. **6 requests de evaluación.**

**`not_yet_cash` sin objetivo — 47 mensajes, 4 sub-tipos:**

| sub-tipo | n | texto | qué hacemos | qué se debe hacer |
|---|---|---|---|---|
| A | 8 | "The next QuickCrew payout is still pending… weekly earnings can change" | suprimir ingreso variable | **correcto** (probado en request_10) |
| B | 12 | "Your quarterly bonus is still pending final performance review" | suprimir variable | correcto por construcción (la nómina fija sobrevive) |
| C | **15** | "**The client approved an invoice payment of IDR 30 780 000. Settlement is expected on 2025-08-15**; the other submitted invoices are not yet confirmed" | se tira el importe Y se suprime la serie | **es un ingreso CONFIRMADO**: añadirlo en su fecha, y suprimir sólo lo demás |
| D | 6 | "The foreign-currency refund is still processing" | suprimir variable | correcto (un reembolso no se cuenta) |
| — | 6 | "**Your confirmed base salary is USD 3072.** The commission shown for open deals is not yet confirmed" | suprimir variable | la base está CONFIRMADA; suprimir sólo la comisión |

Sub-tipos C + los 6 sueltos = **18 requests de evaluación** donde tiramos un ingreso con
importe y fecha explícitos y encima suprimimos la serie. Es doble pérdida.

Advertencia honesta: **ninguno de los sub-tipos A(income_ended), C(not_yet_cash) ni los 6
de "confirmed base salary" aparece en los 25 samples**, así que no puedo medir su signo
contra la verdad de campo. Los reporto como defectos demostrados por el texto del corpus
y por la aritmética de caja, no como mejoras medidas. El implementador debe saberlo.

---

## 9 · CÓMO REPRODUCIR

```bash
cd code
python3 analysis/tabla.py                  # 25 samples vs finance/ (código vivo de A1)
python3 analysis/sweep.py                  # barrido de hipótesis, ayuda/perjudica/igual
python3 analysis/sweep.py -v               # + el detalle por sample
python3 analysis/inverso.py                # el multiplicador k que reproduce cada verdad
python3 analysis/curva2.py request_05      # curva día a día (lab, congelado)
python3 analysis/descomponer.py request_10 # aporte de cada serie al horizonte
python3 analysis/estimadores.py request_15 # historia cruda + los 6 estimadores por serie
python3 analysis/buscar.py request_05      # barrido exhaustivo estimador × conteo
```

`analysis/lab.py` es autónomo: no importa nada de `finance/`, sólo `loader` y
`extraction.facts` (que lee de `code/cache/`, sin llamadas al modelo).

Huella de `finance/` cuando tomé la línea base (A1 seguía editando):
`params.py md5 df2d1c0c…` → cambió a `e642d5a1…` a mitad de mi sesión.
