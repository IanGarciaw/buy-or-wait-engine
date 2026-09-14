# A2 · Motor de decisión — nota de entrega

**KPI (`python3 -m decision.calibrar`, 25 samples, usuarios disjuntos de la evaluación).**
Modo ORÁCULO —capacidad de A1 sustituida por la del ground truth, para aislar A2—:
**status 25/25 · method 25/25 · plan 25/25 · earliest 25/25 · cambios 25/25**, y 23/25
explicaciones idénticas al texto del ground truth. Modo A1 (integrado, hoy): 20/21/20;
la diferencia es el forecast, no la decisión. 34/34 falsadores en verde
(`python3 -m decision.test_decision`), y las 250 solicitudes reales corren sin excepción
con 14 invariantes del contrato comprobadas fila por fila.

**La regla que el corpus impuso.** La lectura literal —simular el plan sobre la curva de
90 días— la refuta request_02: el ground truth elige 3 cuotas que hunden el saldo bajo el
mínimo antes de la tercera. Lo que sí sostienen los 25 samples sin excepción es una prueba
de presupuesto mensual: **cada pago ≤ `amount_safe_to_pay`**, y los únicos 3 planes que la
exceden son exactamente los 3 en que el ground truth añadió cambios de gasto. El ahorro de
un cambio cuenta UNA vez (mensual): contarlo acumulado a 90 días elegiría `entertainment`
donde el ground truth eligió `dining`.

**El piso de 90 días (auditoría D1 de A0).** Tenía razón: `is_safe` existía y no se
invocaba, así que ningún plan tenía techo de cuánto podía hundir el saldo. Ahora la brecha
contra `minimum_balance_to_keep` es la PRIMERA llave del orden: un plan que rompe el piso
pierde contra cualquier plan limpio, y si todos rompen gana el que menos rompe (punto b,
"the financially safer interpretation"). El ahorro de los cambios de gasto cuenta, en las
fechas en que los cobros dejan de ocurrir. El piso NO se aplica a `full_payment` de hoy,
`wait` ni `partial_payment`: en esas tres el piso ya está garantizado por la definición de
`amount_safe_today` y `earliest_full_payment`, y volver a juzgarlas con una curva
reconstruida sólo añade el error de la curva — medido, cuesta 5 de 25 en modo oráculo, y
los planes que "rompen" son los del propio ground truth.

**Clases de error abiertas.**
1. *Plan tardío sin alternativa.* Filtro duro `último pago ≤ desired_completion_date`
   (el enunciado lo exige dos veces fuera del ranking). Ningún sample mide el caso en que
   ése es el único plan; si el ground truth oculto ahí dijera `wait`, pierdo esas filas.
2. *Cuotas que rompen el piso sin alternativa limpia.* 28 de las 250 filas eligen un plan
   que hunde el saldo bajo el mínimo porque es el ÚNICO elegible. El filtro duro las
   convertiría en `not_recommended`; la única observación del corpus sobre ese conflicto
   —request_02— dice que el ground truth prefiere el plan que rompe. Decisión de A0.
3. *Dirección inversa de la regla 1.* `not_affordable` ⇒ fecha vacía, sí. La inversa
   (fecha con valor ⇒ nunca `not_affordable`) NO se aplica: obligaría a recomendar `wait`
   a los 102 usuarios que no aceptan `full_payment`, y el enunciado lo prohíbe.
4. *Cadencia de series flexibles.* Se infiere del histórico (mediana de huecos: 30/31, 21,
   14, 7 días). Si A1 proyecta otra cadencia, el ahorro estimado se desvía del suyo.
5. *Variante de prosa en `not_recommended`.* Dos plantillas en el ground truth; se elige
   por el peso del importe disponible (umbral 8%, hueco medido 4.8%–11%). Base delgada:
   7 casos. Sólo afecta al texto, ningún campo estructurado depende de ello.
