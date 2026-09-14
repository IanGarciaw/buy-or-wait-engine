# FRENTE B — AUDITORÍA DE ESPECIFICACIÓN

Contrato: `problem_statement.md` + `AGENTS.md §6.3`, texto oficial. No se usó ninguna
interpretación previa del equipo como referencia.
Todo lo que sigue se midió corriendo código en `code/audit/` contra `dataset/`. Nada
fuera de `code/audit/` fue modificado.

Scripts de evidencia: `contar.py`, `contar2.py`, `contar3.py`, `experimento.py`,
`experimento2.py`, `experimento3.py`.

---

## RESUMEN — las 3 de mayor impacto (12 líneas)

1. **El piso de 90 días no gobierna el plan.** `decision/safety.py::is_safe` existe y
   nunca se llama: `worst_balance` sólo aparece dentro de un string de traza
   (`engine.py:54`). El único filtro real es "cada pago ≤ amount_safe_to_pay".
   Medido: **31 de 155 planes recomendados en los 250 rompen `minimum_balance_to_keep`
   sin traer cambios de gasto** (4 de 14 en los 25 samples). request_89 queda
   3,897,825.64 IDR bajo el piso. El enunciado lo exige dos veces, literal.
2. **`linked_event_id` se parsea y jamás se lee.** 58 filas; clasificadas aquí en 7
   ciclos de vida. 52 caen bien sólo por `status`; las **6 "Possible duplicate card
   charge"** el código las RESERVA como débito pendiente, y el enunciado manda
   "Ignore … duplicate records". Afecta 6 de los 250 y **0 de los 25** → rama que el
   corpus no puede falsar.
3. **`not_affordable` borra una fecha que el enunciado declara independiente de las
   preferencias.** `engine.py:75` pone `earliest=None`; el enunciado dice que esa fecha
   "measures financial capacity independently of the user's payment-method preferences".
   Medido: **15 de los 250** pierden una fecha de capacidad que el motor sí calculó.

---

## D1 — El plan recomendado no pasa por la prueba de 90 días

```
SPEC dice:      "A recommendation is safe only if the user can make every listed
                payment, complete the full request by its deadline, cover essential
                expenses, and maintain their preferred minimum balance throughout the
                forecast period."
                "A plan is safe only if the balance never falls below
                `minimum_balance_to_keep`."
                "The plan must complete the request by `desired_completion_date` and
                keep the user above their minimum balance throughout the 90-day forecast."

CÓDIGO hace:    decision/candidates.py:107  cap = capacidad(safe)
                decision/candidates.py:119  if cabe(pagos, cap)      <- único filtro
                decision/candidates.py:151  if cabe(pagos, cap)      <- único filtro
                decision/capacidad.py:37-39 cabe() = todo pago ≤ cap
                decision/safety.py:63-65    is_safe() NUNCA se invoca desde
                                            candidates.py ni engine.py
                decision/engine.py:54       worst_balance() se usa sólo para escribir
                                            "curva90=..." dentro de un string de traza

DIVERGENCIA:    la prueba de seguridad que el enunciado define sobre la CURVA de 90 días
                se sustituyó por una prueba de presupuesto mensual, y la de curva quedó
                como comentario en la traza, sin poder bloquear nada.

AFECTA:         4 de 25 samples · 36 de 250 requests (31 de ellos sin cambios de gasto
                que pudieran justificarlo)

EVIDENCIA:      experimento2.py / experimento3.py
                25 samples : 4 de 14 planes recomendados rompen el piso
                250        : 31 de 155 planes recomendados rompen el piso SIN cambios
                request_89  installments  min curva  8,636,874.36 < mínimo 12,534,700.00
                request_02  installments  min curva 16,502,907.77 < mínimo 29,158,400.00
                request_75  installments  min curva     20,995.88 < mínimo     39,000.00
                request_99  full_payment  min curva     47,775.51 < mínimo     49,100.00
                El propio verificador del repo ya lo grita: `WARN[floor] piso roto`.
```

**Matiz honesto, no descargo:** `decision/capacidad.py:1-25` documenta que esto fue
deliberado, porque la verdad de campo de request_02 elige 3 cuotas que rompen la curva.
Eso es un conflicto real spec-vs-corpus. Pero la decisión que se tomó no fue "relajar el
piso", fue **quitarlo entero**: no hay techo de cuánto puede hundirse un plan. Un piso con
tolerancia medida sería una decisión; cero compuerta es otra cosa.

---

## D2 — `linked_event_id` no se lee nunca; los 6 duplicados se cuentan como deuda

```
SPEC dice:      "When `linked_event_id` is present, it points to an earlier event in the
                same transaction or investment lifecycle."
                "Ignore pending credits, failed or cancelled transactions, duplicate
                records, and unrealized investments."
                Precedencia 1: "An explicit cancellation, settlement, or amendment"

CÓDIGO hace:    loader.py:80       linked_event_id se parsea
                contracts.py:86    se guarda en el dataclass Event
                grep -rn "linked" finance/ decision/ extraction/ main.py -> 0 resultados
                finance/view.py:249-250  status=="pending" -> when = max(day, first)
                                         (todo débito pendiente se reserva, sin mirar
                                          si es el duplicado de un cargo ya liquidado)

DIVERGENCIA:    el campo que el enunciado da para reconstruir el ciclo de vida de una
                transacción no participa en ninguna decisión; las 6 filas cuya propia
                descripción dice "Possible duplicate card charge" y cuyo padre liquidado
                tiene EL MISMO importe se cobran como una segunda salida de caja.

AFECTA:         6 de 250 requests (138, 156, 198, 210, 234, 252) · 0 de 25 samples
                (las 58 filas con link tocan 58 usuarios distintos)

EVIDENCIA:      contar2.py (clasificación completa) y contar3.py
                event_12709/user_138  134.75   <- padre event_12708 "Original card charge" 134.75
                event_14399/user_156  1617.00  <- padre event_14398  1617.00
                event_18269/user_198  8800.00  <- padre event_18268  8800.00
                event_19334/user_210  145.80   <- padre event_19333  145.80
                event_21582/user_234  45.65    <- padre event_21581  45.65
                event_23203/user_252  988000   <- padre event_23202  988000
                Los 6 caen DENTRO del horizonte de 90 días de su request.
                experimento.py variante D: ignorarlos altera 0 de los 25 samples
                (±1%=4/25, ±5%=11/25, idéntico al baseline) -> el corpus de muestra NO
                puede decidir esta rama. **Es una apuesta sin prueba, no una regla medida.**
```

**Clasificación real de las 58 filas con `linked_event_id`** (contar2.py, sección D).
La consigna era no asumir "linked == duplicado". No lo es: son 7 ciclos distintos.

| # | padre → hijo | qué es | qué hace el código | ¿correcto? |
|---|---|---|---|---|
| 14 | settled expense → settled refund (debit→credit) | "Card charge later reversed" / "Employer expense reimbursement", mismo importe | ambos al histórico; el refund NO forma serie (`series.py:200-201` rechaza credit no-income) | el efectivo sí; ver **D3** por el lado del estimador |
| 10 | settled investment_purchase → unrealized valuation (debit→non_cash) | revaluación de cartera | `view.py:231` descarta `non_cash` y `unrealized` | ✅ |
| 8 | **cancelled** authorization → settled purchase | autorización anulada + cobro real, mismo importe | `view.py:231` descarta `cancelled`; cuenta sólo el liquidado | ✅ |
| 8 | settled purchase → **pending** refund (debit→credit) | devolución del comercio aún no liquidada | `view.py:247-248` no la cuenta | ✅ (son los 8 únicos pending credit del dataset) |
| 7 | **failed** debt_payment → **scheduled** retry | pago fallido + reintento programado | descarta el fallido, cuenta el reintento en su fecha | ✅ |
| 6 | settled "Original card charge" → **pending** "Possible duplicate card charge" | cargo duplicado en disputa | **lo reserva como débito pendiente** | ⚠️ **D2** |
| 5 | settled investment_purchase → settled investment_sale | venta de inversión | al histórico; no forma serie de ingreso | ✅ |

El contra-argumento del código vive en `view.py:126-132`: los mensajes de esos 6 dicen
"A reversal has not been posted to the account yet. The dispute is open". Es un argumento
serio — por eso esto es una divergencia a resolver, no un bug obvio. Pero el enunciado
resuelve el empate en el otro sentido y el código nunca cita esa frase.

---

## D3 — Un cargo revertido alimenta el estimador de gasto recurrente; su reverso no lo netea

```
SPEC dice:      "Distinguish recurring expenses from one-time purchases, transfers,
                refunds, and unusual events."

CÓDIGO hace:    finance/series.py:249-254  agrupa el histórico SÓLO por
                               (direction, event_type, category)
                finance/series.py:199-201  una serie `credit` que no sea `income`
                               devuelve None -> el refund nunca proyecta ni netea
                No existe ningún filtro que saque del histórico un gasto puntual que
                fue revertido o reembolsado.

DIVERGENCIA:    el débito original (categoría shopping / work_expense) entra al
                estimador de la serie de gasto como si fuera consumo recurrente; el
                crédito que lo cancela queda fuera por ser refund. La asimetría infla
                el gasto proyectado.

AFECTA:         22 eventos originales (15 shopping + 7 work_expense) en 22 usuarios
                distintos, de los cuales 14 ya fueron revertidos/liquidados y 8 esperan
                devolución

EVIDENCIA:      contar3.py, sección "refunds settled pasados que alimentan series"
                refunds: 22 -> settled/credit 14, pending/credit 8
                categorías de los ORIGINALES que sí entran a la serie:
                  shopping 15, work_expense 7
```

No pude aislar el delta en `safe` por request dentro del tiempo asignado: el efecto pasa
por el estimador de serie, no por un flujo. Queda como divergencia con conteo, sin cifra
de impacto en euros. **No la presento como probada en magnitud, sólo en existencia.**

---

## D4 — `not_affordable` borra una fecha que el enunciado declara independiente

```
SPEC dice:      "`earliest_date_for_full_payment` measures financial capacity
                independently of the user's payment-method preferences."
                "Leave it empty when the full amount is not expected to become safe
                within the forecast period."

CÓDIGO hace:    decision/engine.py:75   if estado == "not_affordable": earliest = None

DIVERGENCIA:    `not_affordable` se produce también cuando SÍ hay capacidad pero ningún
                método es elegible (el usuario no acepta nada / el plazo no llega). El
                código trata "no hay plan" como "no hay capacidad" y borra la fecha que
                el enunciado define justo como ajena a las preferencias.

AFECTA:         3 de 25 samples · 15 de 250 requests

EVIDENCIA:      code/audit/experimento4_earliest.py (mismo import que el motor)
                25 samples : not_affordable=11, con fecha calculada y borrada = 3
                250        : not_affordable=95, con fecha calculada y borrada = 15
```

El propio comentario `engine.py:68-76` reconoce que la bicondicional no se sostiene y la
aplica igual en una dirección. Confianza media: con los 7 `not_affordable` de la verdad
de campo la regla acierta, pero el motor produce 11 (no 7) y arrastra la borradura a
casos que el corpus no cubre.

---

## D5 — VERIFICADO LIMPIO: el ahorro NO contamina `amount_safe_to_pay`

Era el punto 3 del encargo. **No hay divergencia. Lo desmiento con líneas.**

```
SPEC dice:      "`amount_safe_to_pay`: the most the user can pay today BEFORE optional
                spending changes without breaking the 90-day safety check, capped at
                `requested_amount`."

CÓDIGO hace:    finance/view.py:463-465   head = fc.headroom(min_balance)
                                          safe = min(requested_amount, head)
                contracts.py:210-212      headroom = max(0, min_balance_90d - minimo)
                  -> la curva de `curve(rec)` (view.py:458) se construye SIN aplicar
                     ningún stop ni reduce_to: `simulate()` (view.py:516) es la única
                     función que los aplica y no participa en build_view.
                decision/engine.py:59     safe = min(max(0, view.amount_safe_today),
                                                     requested_amount)
                decision/candidates.py:107 cap = capacidad(safe)   <- ahorro por defecto 0
                decision/capacidad.py:33-34 capacidad(safe, ahorro=0)

                El ahorro entra EXCLUSIVAMENTE en decision/candidates.py:90-94
                (`_cerrar_hueco`), después de construir el plan, y nunca vuelve a `safe`:
                el Decision se rellena con `safe`, no con `cap` (engine.py:80).

VEREDICTO:      correcto. Una mutación que pasara `capacidad(safe, ahorro)` a la línea
                107 inflaría `safe`; hoy no ocurre en ninguna ruta.
```

---

## D6 — Matriz de estados, celda por celda (punto 4)

Construida contra el dato real, no contra decisiones heredadas. Conteos: `contar.py`.

Universo medido en `financial_events.csv` (25,342 filas):
`settled` 25,148 · `pending` 71 · `scheduled` 70 · `cancelled` 22 · `failed` 21 · `unrealized` 10.
Direcciones: `debit` 23,609 · `credit` 1,723 · `non_cash` 10.
**Celdas que el dataset no usa:** cancelled/credit, failed/credit, unrealized/credit,
unrealized/debit, settled/non_cash, pending/non_cash, scheduled/non_cash — vacías.

| status | dir | < request_date | == request_date | > request_date | línea | frase del enunciado |
|---|---|---|---|---|---|---|
| settled | credit | histórico, alimenta cadencia; NO toca el saldo | (no ocurre) | contado en su fecha | view.py:239-240 / 253-258 | "Count confirmed salary on its settlement date" |
| settled | debit | histórico, alimenta cadencia | (no ocurre) | contado | view.py:239-240 | "Detect recurrence only when history supports it" |
| pending | credit | (no ocurre) | (no ocurre) | **NO se cuenta** | view.py:247-248 | "Do not count pending credits … until they settle" ✅ |
| pending | debit | (no ocurre) | (no ocurre) | **se reserva** en `max(day, request_date)` | view.py:249-250 | "Reserve pending debits" ✅ |
| scheduled | credit | — | — | contado en su fecha | view.py:253-258 | "Count confirmed salary on its settlement date" ✅ |
| scheduled | debit | — | — | contado en su fecha | view.py:253-258 | "confirmed future payments" ✅ |
| cancelled | debit | descartado | — | (no ocurre) | view.py:30, 231 | "Ignore … cancelled transactions" ✅ |
| failed | debit | descartado | — | (no ocurre) | view.py:30, 231 | "Ignore … failed … transactions" ✅ |
| unrealized | non_cash | descartado | — | (no ocurre) | view.py:30, 231 | "Ignore … unrealized investments" ✅ |

**Lo que la matriz revela y no se sabía:**

- **No hay un solo evento `settled` con fecha ≥ `request_date`** en ninguno de los 275
  usuarios (contar3.py). El riesgo de doble conteo del punto 2 **no existe en este
  corpus**, y el código además no lo tendría: `view.py:239-240` manda todo `settled`
  pasado al histórico y nunca al saldo. La curva arranca en
  `profile.current_available_balance` tal cual (`view.py:427`). Demostrado en contar2.py
  sección C: `user_01` tiene balance 58,481.10 y su suma de settled históricos es
  **-101,663.83**; `user_03` balance 5,810,300 contra +9,484,772.58. El saldo es un estado
  dado, no la suma de la historia — reaplicarla sería absurdo y el código no lo hace.
- **Todos los 47 `scheduled/credit` son literalmente "Next confirmed salary"** (contar3.py).
  No hay ningún bono ni comisión `scheduled` que el código estuviera contando de más. La
  prohibición de §6.3 ("bonuses, commissions, prizes") no tiene con qué dispararse aquí.
- **Todos los 8 `pending/credit` son "Pending merchant refund"**, los 8 con
  `linked_event_id`.
- **`cancelled` / `failed` / `unrealized` son 100 % pasados.** La compuerta `DEAD`
  (`view.py:30`) no impide ni un solo flujo futuro. Medido: contarlos como `settled`
  (experimento.py variante G) o convertir los `unrealized` en crédito liquidado
  (variante H) deja el marcador **exactamente igual: ±1%=4/25, ±5%=11/25**.
  → **Compuerta que no puede dispararse** (garantía 3). Es spec-correcta, pero está sin probar.

**Asimetría débito/crédito pendiente (punto 1): el código la implementa y el corpus la
confirma.** Falsificación medida (experimento.py):

| variante | ±1% | ±5% |
|---|---|---|
| A) baseline | 4/25 | **11/25** |
| B) el débito pendiente NO se reserva | 4/25 | **8/25** ← peor |
| C) el crédito pendiente SÍ se cuenta (simétrico) | 4/25 | 11/25 ← **sin cambio** |
| E) el crédito `scheduled` no se cuenta | 3/25 | 10/25 ← peor |

Reservar el débito pendiente mueve 5 samples (request_02, 03, 20, 22, 23) y en los 5 acerca
a la verdad: request_02 gt 17,229,139.2 → con reserva 17,551,231.37 (err 1.87 %), sin
reserva 19,202,331.37 (err 11.45 %). **Confirmado, no heredado.**
La otra mitad — no contar el crédito pendiente — **no cambia ni un sample** (variante C):
el único pending credit de los 25 cae en un request cuyo `safe` ya topa en
`requested_amount`. Es spec-correcta y **no falsable con este corpus**.

---

## D7 — Fronteras de fecha (punto 5): inclusivo en ambos extremos, sin evento de CSV en la frontera

```
CÓDIGO hace:    finance/params.py:81      HORIZON = 90
                finance/params.py:83      INCLUDE_DAY_ZERO = True
                finance/view.py:215-216   end = as_of + 90 ; first = as_of
                finance/view.py:253       if when < first or when > end: continue
                                          -> [request_date, request_date+90] CERRADO
                finance/view.py:429-435   curve() emite 91 puntos, de as_of a as_of+90
                decision/safety.py:22-23  horizon_end = start + 90 (mismo criterio)

SPEC dice:      "Forecast the user's balance for the next 90 days" — no fija si el día
                del request y el día 90 entran. El enunciado no lo resuelve.

AFECTA:         0 eventos de CSV en cualquiera de las dos fronteras, en los 25 y en los 250.
                147 flujos PROYECTADOS caen exactamente en request_date y 170 exactamente
                en request_date+90 (250 requests); 14 y 19 respectivamente en los 25.

EVIDENCIA:      contar.py (fronteras de CSV: vacío en las 2 × 6 celdas)
                experimento2.py (flujos): 250 -> día 0: 147 · día 90: 170
                experimento3.py (sensibilidad):
                  HORIZON=90, INCLUDE_DAY_ZERO=True  ±1%=4/25  ±5%=11/25  (actual)
                  INCLUDE_DAY_ZERO=False             ±1%=4/25  ±5%=10/25
                  HORIZON=89                         ±1%=4/25  ±5%=11/25
                  HORIZON=91                         ±1%=4/25  ±5%=10/25
```

**Conclusión: la frontera NO explica ningún `safe` desviado.** La hipótesis del encargo
("un error de frontera puede explicar varios safe") queda **refutada**: mover cualquiera
de los dos extremos un día cuesta como mucho 1 sample, y ninguno de los dos extremos
contiene un evento del CSV. **HEURISTIC** igualmente: `INCLUDE_DAY_ZERO` y el cierre
derecho no se derivan de ninguna frase; son elecciones.

---

## D8 — FX (punto 8): la regla declarada es correcta; los tres respaldos son código muerto

```
SPEC dice:      "For a foreign-currency cash event, use the row for its settlement date
                and the stated from_currency to to_currency direction."

CÓDIGO hace:    finance/view.py:46-47   _day_of(e) = settlement_date or event_date
                finance/view.py:237     _home(amt, e, prof, day, ds)  <- FECHA DE
                                        LIQUIDACIÓN ✅
                loader.py:172-173       rates[(on, frm, to)]          <- dirección
                                        declarada primero ✅
                loader.py:174-175       si no, INVIERTE con rates[(on, to, frm)]
                loader.py:177-183       si no, la fila anterior más cercana (ambas dirs)
                loader.py:184           return amount   <- 1:1 SILENCIOSO

AFECTA:         140 eventos en moneda distinta a la home, en 27 de los 275 usuarios
                (131 settled/credit, 8 scheduled/credit, 1 settled/debit).
                Dentro del horizonte: 7 de los 250 requests, 1 de los 25.

EVIDENCIA:      contar3.py
                "FX con fila exacta dir declarada: 140 | sólo dir inversa: 0 | sin fila
                 esa fecha: 0"
                Pares en exchange_rates.csv: EUR->ZAR 22, USD->EUR 25, USD->IDR 30,
                USD->INR 33, EUR->USD 24. El único par con ambas direcciones es
                USD/EUR — y ningún evento necesita la inversa.
```

**Veredicto: la pregunta del encargo ("¿usa liquidación o evento? ¿invierte?") se responde
a favor del código.** Usa liquidación y la dirección declarada, y los 140 eventos la
encuentran. Pero eso deja tres ramas que **nunca se ejecutan** (loader.py:174-183) y una
cuarta (loader.py:184) que **devuelve el importe sin convertir en silencio** si nada
coincide: si el conjunto oculto trae una fecha sin fila, el sistema no falla — miente
en la moneda equivocada. **HEURISTIC + garantía 3.**

Segunda regla de FX, sin respaldo en el enunciado: `finance/view.py:115` convierte el
importe de un hecho de mensaje con `eff or as_of` — la fecha efectiva o la del request,
**no** una fecha de liquidación. Es una segunda política de FX que el enunciado no autoriza.

---

## D9 — Precedencia en conflictos (punto 7): de las cuatro reglas, el código implementa cero como orden

```
SPEC dice:      "When records conflict, prefer:
                 1. An explicit cancellation, settlement, or amendment
                 2. A newer record from the same source
                 3. A settled event over an estimate or forecast
                 4. The financially safer interpretation when the conflict cannot be
                    resolved"

CÓDIGO hace:    No existe ninguna función de resolución de conflictos con estas cuatro
                reglas ni con un orden entre ellas.
                · Regla 1: sólo indirecta, vía `status` (view.py:30, 231) y vía hechos de
                  mensaje kind=="cancellation" (view.py:117-120). `linked_event_id`, que
                  es el campo que declara "enmienda de X", no se lee (ver D2).
                · Regla 2: NO implementada. `loader.py:112-121` conserva el orden del CSV
                  y nada ordena por `sent_at`; `grep -rn sent_at finance/ decision/` = 0.
                  En `view.py:143-161` varios hechos de tipo income_change / amount_amendment
                  SOBRESCRIBEN `a.ingreso_monto` en el orden en que aparecen: gana el
                  ÚLTIMO DEL ARCHIVO, no el más nuevo.
                · Regla 3: NO implementada como regla. Emerge de que `settled` pasado va al
                  histórico, pero nunca se compara un settled contra un estimado.
                · Regla 4: NO implementada. La frase "la interpretación financieramente más
                  segura" aparece una sola vez en el repo y es un COMENTARIO
                  (view.py:126-132), no una rama de código.
                · `_dedupe` (view.py:397-417) NO es un deduplicador de conflictos: resuelve
                  serie-proyectada contra evento-confirmado con una ventana de ±15 días.

DIVERGENCIA:    la lista numerada del enunciado no tiene contraparte ejecutable. Donde hay
                empate, gana el orden del archivo — que es el atajo "el último gana", peor
                que el atajo "el más nuevo gana" que el encargo sospechaba.

AFECTA:         no cuantificable como filas erradas sin la verdad oculta. Superficie:
                215 mensajes sobre 275 usuarios; 58 filas con linked_event_id; 43 eventos
                cancelled+failed cuyo vínculo con su original no se usa.

EVIDENCIA:      grep -rn "sent_at" finance/ decision/ main.py -> 0 resultados
                grep -rn "linked" finance/ decision/ extraction/ main.py -> 0 resultados
                view.py:143-161: `a.ingreso_monto, a.ingreso_desde = amt, eff` sin
                ninguna comparación de antigüedad ni de fuente.
```

Para un débito, "la interpretación más segura" es la de mayor salida; para un crédito, la
de menor o más tardía entrada. Ninguna de las dos está escrita. En D2 el código elige por
accidente la lectura segura del débito (reserva el duplicado) y en D1 elige la insegura
(no comprueba el piso). No es una política: es el resultado de dos decisiones
independientes que nadie confrontó.

---

## HEURISTIC — constantes y reglas que no mapean a ninguna frase del enunciado

El encargo no pide eliminarlas, pide identificarlas. Inventario completo (experimento3.py §3).
`finance/params.py` documenta la medición de muchas de ellas; documentada **no** es lo
mismo que derivada del contrato — todas las de abajo son **HEURISTIC**.

**Multiplicadores y factores**
- `VARIABLE_MULT = 1.06` — **HEURISTIC**. Su respaldo textual es la palabra
  "conservatively"; el 6 % es un barrido sobre 25 samples. El enunciado no da número.
- `STALE_FACTOR = 1.2`, `STALE_SLACK = 5` — **HEURISTIC**. "Detect recurrence only when
  history supports it" no define cuándo una serie muere.
- `NUEVO_RECURRENTE_MAX_FRAC = 0.5` — **HEURISTIC**. Regla inventada para descartar un
  hecho mal tipado.
- `INTERVAL_TOL = 0.34`, `INTERVAL_AGREE = 0.6` — **HEURISTIC**.
- `Decimal("30.44")` en `candidates.py:78` — **HEURISTIC** (días por mes promedio).

**Ventanas**
- `LOOKBACK = 8` ocurrencias; `LOOKBACK_DAYS = 0` — **HEURISTIC**.
- `MONTH_MIN, MONTH_MAX = 26, 32` (view) y `27 <= med <= 32` (`decision/series.py:74`,
  `candidates.py:76`) — **HEURISTIC y además INCONSISTENTES entre sí**: 26 días es mensual
  para el forecast y no lo es para los cambios de gasto.
- `_dedupe` ventana de **15 días** o `period // 2` (`view.py:404`) — **HEURISTIC**.
- `_dias_ingreso`: `(nueva - d).days > 25` (`view.py:387`) — **HEURISTIC**.
- `k < 12` repeticiones en `_mensual` / `_dias_ingreso` — **HEURISTIC**.
- `HORIZON = 90` es del enunciado; `INCLUDE_DAY_ZERO = True` **no** — **HEURISTIC**.

**Umbrales**
- `MIN_OCCURRENCES = 3` y `MIN_OCCURRENCES_INCOME = 2` — **HEURISTIC**.
- `MAX_CHANGES = 3` (`changes.py:30`) — **respaldado**: "may contain up to three changes".
- `FACT_MIN_CONFIDENCE = 0.0` — **HEURISTIC**, y además desactiva su propia compuerta:
  ningún hecho puede ser rechazado por baja confianza. Compuerta que no puede dispararse.
- `_id_num` → `10**9` sin opción (`ranking.py:37`) — **HEURISTIC** (el criterio 6 del
  enunciado no dice qué hacer con un plan sin `payment_option_id`).

**Listas de palabras** (ninguna aparece en el enunciado; son vocabulario del dataset)
- `INCOME_TERMINAL = ("final ", "previous ", "last ")` — **HEURISTIC**.
- `INCOME_NOT_RECURRING` — 11 palabras. Parcialmente respaldada: §6.3 nombra bonuses,
  commissions, refunds, lottery proceeds, investment gains. **"prorated", "arrears",
  "promotion", "one-time" son HEURISTIC**.

**Interruptores de interpretación de mensajes** (`view.py:117-179`) — los cinco son
**HEURISTIC**: `FACT_ENMIENDA_SIN_OBJETIVO_ES_INGRESO=True`, `FACT_NUEVO_RECURRENTE="gasto"`,
`FACT_NOT_YET_CASH_SIN_OBJETIVO="suprimir_variable"`,
`FACT_INGRESO_TERMINADO_CON_OBJETIVO="descartar_evento"`, `DEDUPE="todos"`.
El más grave es el primero: convierte una `amount_amendment` huérfana en **un cambio de
sueldo**. El enunciado dice "Do not invent unsupported income".

**Reglas estructurales sin frase que las respalde**
- `finance/series.py:249-254`: agrupar por `(direction, event_type, category)` e ignorar
  la descripción. Es la decisión de mayor impacto del sistema y es **HEURISTIC**.
- `decision/capacidad.py`: "cada pago ≤ amount_safe_to_pay" como definición de seguro.
  **HEURISTIC, y contradice el texto** (ver D1).
- `decision/changes.py:65-84`: elegir el subconjunto de **menor ahorro total**.
  El enunciado no ordena los cambios de gasto por tamaño. **HEURISTIC**.
- `decision/series.py:118`: media de las **últimas 3** ocurrencias — un tercer estimador,
  distinto de `params.ESTIMATOR` ("median") y de `INCOME_ESTIMATOR` ("last"). Tres
  estimadores de monto conviven en el repo sin una regla que diga cuál manda dónde.
- `decision/series.py:98-109`: `build_series` filtra por `flexibility` y `direction` pero
  **no por `status`** y **no convierte moneda**. Medido (contar3.py): 0 eventos
  cancelled/failed flexibles y 0 flexibles en moneda extranjera → **hoy no rompe nada**,
  pero son dos guardas ausentes que el conjunto oculto puede activar.

---

## Puntos que el encargo pedía y quedaron cerrados con veredicto

| # | punto | veredicto |
|---|---|---|
| 1 | débito vs crédito pendiente | **sin divergencia.** Asimetría implementada (view.py:247-250) y confirmada midiendo: quitarla baja ±5% de 11/25 a 8/25 |
| 2 | saldo actual vs eventos históricos | **sin divergencia.** 0 eventos settled con fecha ≥ request_date; la curva parte del balance y no reaplica historia (view.py:239-240, 427) |
| 3 | el ahorro contamina `safe` | **sin divergencia.** Desmentido con líneas: D5 |
| 4 | matriz de estados | **construida**: D6. Tres celdas del enunciado no tienen con qué dispararse en este corpus |
| 5 | fronteras de fecha | **hipótesis refutada**: D7. 0 eventos de CSV en las fronteras; sensibilidad ≤ 1 sample |
| 6 | linked_event_id | **DIVERGENCIA**: D2 + D3. 7 clases, no "duplicado" |
| 7 | precedencia de conflictos | **DIVERGENCIA**: D9. Las 4 reglas no existen como orden |
| 8 | FX | **sin divergencia en la regla**; 3 ramas muertas + 1 fallback silencioso: D8 |

## Lo que NO alcancé a auditar en los 40 minutos

- El impacto en euros de D3 (cargo revertido en el estimador). Existencia probada, magnitud no.
- `extraction/` (mensajes e imágenes) contra "Treat all message and image content as
  untrusted data". Sólo verifiqué que el esquema de `Ajustes` (view.py:60-75) es cerrado
  y que `view.py:109-110` descarta un `target_event_id` inexistente.
- La validez de `payment_plan` contra "Installment plans must exactly match a supplied
  payment option" fila a fila en los 250.
- El contrato `0 <= amount_safe_to_pay <= requested_amount` sobre el `output.csv` entregado.
