# CORRELACIONES — Frente C, análisis de patrones (35 min)

Metodología: `amount_safe_to_pay` obtenido con `python3 code/main.py --samples` (motor real,
caché de hechos en `code/cache/`), comparado contra `dataset/sample_requests.csv` con
tolerancia **±1%** (no el 5% que usa el verificador interno). Resultado: **6/25 exactos**
(request_01, 08, 09, 14, 16, 19), **19/25 fallados**. Scripts y matriz completa en
`code/patterns/matriz.csv` / `matriz.json`.

## RESUMEN — LO QUE LEO PRIMERO (10 líneas)

1. **`safe_capado_a_requested` es el mayor "diferenciador" (3/6 exactos vs 1/19 fallados,
   diff=0.45) — y es un ARTEFACTO, no una pista.** Cuando GT_safe == requested_amount no hay
   valle que calcular: sólo hay que capar. 3 de los 6 exactos (01, 09, 16) ganan por eso, no
   porque el motor acierte el cálculo difícil.
2. Al remover esos casos (control, n=21, sólo 0<GT_safe<requested) sólo quedan **3 exactos**:
   08, 14, 19.
3. En el grupo control, los dos rasgos con más contraste son **`tiene_pending_debit`** y
   **`tiene_scheduled`**: 0/3 (0%) entre exactos vs 6/18 (33%) entre fallados cada uno — pero
   n=3 en el lado exacto es una base muy delgada, es pista, no conclusión.
4. **`GT_tiene_cambios_de_gasto` es 0% en TODOS los exactos** (0/6 en los 25, 0/3 en control) y
   ~16-17% en fallados en ambos cortes — el único rasgo 100% consistente en las dos vistas:
   ningún caso que requiere `spending_changes_needed != none` acierta el safe.
5. **`tiene_hecho_accionable` (mensaje con hecho != none) parece señal en los 25** (33% vs 63%,
   diff=0.30) **pero se desvanece en el control** (67% vs 61%) — es un espejismo causado por el
   mismo artefacto del punto 1 (los capados casi no tienen hechos accionables). Se descarta.
6. `n_series_recurrentes` es 100% en ambos grupos en ambas vistas — no discrimina nada, es
   propiedad universal del dataset (todo usuario tiene ≥1 categoría con ≥3 ocurrencias en 90
   días).
7. `n_categorias_flexibles` casi universal (94-100% en fallados, 67-100% en exactos según
   corte) — ruido, no señal limpia.
8. `tiene_mensajes` estable ~50-74% en ambos grupos en ambos cortes — no discrimina.
9. `tiene_moneda_extranjera`, `tiene_pending_credit`, `tiene_unrealized` son raros en la
   muestra (≤2 casos totales) — no hay base para sacar conclusión, ni a favor ni en contra.
10. Conclusión operativa: la pista con más apoyo cruzado es **pending/scheduled events dentro
    del horizonte de 90 días** (apunta a `finance/` — cómo se cuentan pendientes y programados
    en el forecast), seguida de **cambios de gasto no implementados** (`decision/` — spending
    changes). No hay evidencia de causalidad, sólo proporción.

---

## ENTREGA 1 — Matriz de características

25 filas × 18 columnas + etiqueta, en `code/patterns/matriz.csv` y `matriz.json`. Definiciones
usadas (todas a nivel USUARIO, agregando todos sus eventos/mensajes, no sólo el request):

- `tiene_pending_debit/credit`, `tiene_scheduled`, `tiene_cancelled_o_failed`,
  `tiene_unrealized`: conteo de eventos del usuario en `financial_events.csv` con ese
  `status`/`direction`.
- `tiene_linked_event`: conteo de eventos con `linked_event_id` no vacío.
- `tiene_moneda_extranjera`: conteo de eventos con `currency != home_currency` del perfil.
- `tiene_mensajes`: nº de filas en `messages.csv` del usuario.
- `tiene_hecho_accionable`: de esos mensajes, cuántos tienen `MessageFact.kind != "none"` en
  `code/cache/messages.json`.
- `tiene_imagen`: 0/1, el usuario tiene alguna fila en `images.csv`.
- `tiene_monto_en_blanco`: eventos con `amount` vacío en el CSV (van a imagen).
- `n_series_recurrentes`: nº de categorías de evento con ≥3 ocurrencias para ese usuario.
- `n_eventos_totales`: total de eventos del usuario.
- `hay_cambio_de_empleo`: 1 si algún mensaje del usuario tiene kind en
  {income_ended, income_change, income_date_change}.
- `safe_capado_a_requested`: 1 si `GT_safe == requested_amount` (sample_requests.csv).
- `fraccion_safe`: `GT_safe / requested_amount`.
- `n_categorias_flexibles`: eventos con `flexibility != fixed`.
- `GT_tiene_cambios_de_gasto`: 1 si `spending_changes_needed != "none"` en el GT.
- `safe_exacto`: 1 si `|obtenido - GT_safe| / GT_safe <= 1%` (0 si GT_safe==0 se exige
  obtenido==0).

## ENTREGA 2 — Tabla de contraste (los 25 samples)

EXACTOS = {01, 08, 09, 14, 16, 19} (n=6) · FALLADOS = resto (n=19)

| característica              | EXACTOS (n=6) | FALLADOS (n=19) | diff |
|---|---|---|---|
| safe_capado_a_requested      | 3/6 (50%)  | 1/19 (5%)   | 0.45 ** |
| tiene_hecho_accionable       | 2/6 (33%)  | 12/19 (63%) | 0.30 *  |
| n_categorias_flexibles       | 4/6 (67%)  | 18/19 (95%) | 0.28 *  |
| tiene_mensajes               | 3/6 (50%)  | 14/19 (74%) | 0.24    |
| tiene_monto_en_blanco        | 2/6 (33%)  | 3/19 (16%)  | 0.18    |
| tiene_imagen                 | 2/6 (33%)  | 3/19 (16%)  | 0.18    |
| GT_tiene_cambios_de_gasto    | 0/6 (0%)   | 3/19 (16%)  | 0.16    |
| tiene_pending_debit          | 1/6 (17%)  | 6/19 (32%)  | 0.15    |
| tiene_unrealized             | 0/6 (0%)   | 2/19 (11%)  | 0.11    |
| tiene_pending_credit         | 0/6 (0%)   | 1/19 (5%)   | 0.05    |
| tiene_moneda_extranjera      | 0/6 (0%)   | 1/19 (5%)   | 0.05    |
| tiene_linked_event           | 1/6 (17%)  | 4/19 (21%)  | 0.04    |
| hay_cambio_de_empleo         | 1/6 (17%)  | 4/19 (21%)  | 0.04    |
| tiene_scheduled              | 2/6 (33%)  | 6/19 (32%)  | 0.02    |
| tiene_cancelled_o_failed     | 1/6 (17%)  | 3/19 (16%)  | 0.01    |
| n_series_recurrentes         | 6/6 (100%) | 19/19 (100%)| 0.00    |

`**` = diferencia fuerte (≥0.4) · `*` = diferencia notable (≥0.25). Ver ENTREGA 3: el `**` de
arriba (safe_capado) es artefacto, y el primer `*` (tiene_hecho_accionable) también se explica
por el mismo artefacto (ver abajo).

## ENTREGA 3 — Control: sólo 0 < GT_safe < requested_amount (n=21, quita el tope)

Filas capadas al tope (GT_safe == requested_amount): **01, 09, 12, 16** (4 de 25). De ésas, 3
son exactas (01, 09, 16) y sólo 1 falla (12). Es decir, el tope por sí solo casi garantiza el
acierto — confirma que el punto 1 del resumen es artefacto y no señal del subsistema.

Quitando esas 4, quedan 21 samples, de los cuales sólo **3 son exactos: 08, 14, 19** (los otros
18 son los mismos 18 fallados menos el 12, que se fue al grupo capado).

EXACTOS-control (n=3) = {08, 14, 19} · FALLADOS-control (n=18) = resto

| característica              | EXACTOS (n=3) | FALLADOS (n=18) | diff |
|---|---|---|---|
| tiene_pending_debit          | 0/3 (0%)   | 6/18 (33%)  | 0.33 *  |
| tiene_scheduled              | 0/3 (0%)   | 6/18 (33%)  | 0.33 *  |
| tiene_linked_event           | 0/3 (0%)   | 4/18 (22%)  | 0.22    |
| tiene_cancelled_o_failed     | 0/3 (0%)   | 3/18 (17%)  | 0.17    |
| tiene_monto_en_blanco        | 1/3 (33%)  | 3/18 (17%)  | 0.17    |
| tiene_imagen                 | 1/3 (33%)  | 3/18 (17%)  | 0.17    |
| hay_cambio_de_empleo         | 1/3 (33%)  | 3/18 (17%)  | 0.17    |
| GT_tiene_cambios_de_gasto    | 0/3 (0%)   | 3/18 (17%)  | 0.17    |
| tiene_unrealized             | 0/3 (0%)   | 2/18 (11%)  | 0.11    |
| tiene_mensajes               | 2/3 (67%)  | 13/18 (72%) | 0.06    |
| n_categorias_flexibles       | 3/3 (100%) | 17/18 (94%) | 0.06    |
| tiene_pending_credit         | 0/3 (0%)   | 1/18 (6%)   | 0.06    |
| tiene_moneda_extranjera      | 0/3 (0%)   | 1/18 (6%)   | 0.06    |
| tiene_hecho_accionable       | 2/3 (67%)  | 11/18 (61%) | 0.06    |
| n_series_recurrentes         | 3/3 (100%) | 18/18 (100%)| 0.00    |
| safe_capado_a_requested      | 0/3 (0%)   | 0/18 (0%)   | 0.00 (por construcción) |

**Lectura del control (la vista que importa):**

- `tiene_pending_debit` y `tiene_scheduled` son los únicos con diferencia ≥0.25 que sobreviven
  quitar el artefacto del tope. Ningún exacto del control tiene eventos `pending`/`debit` ni
  `scheduled`; un tercio de los fallados sí. Base delgada (n=3 del lado exacto), pero es la
  pista más limpia que queda.
- `tiene_hecho_accionable` cae de diff=0.30 a diff=0.06 al quitar los capados: confirma que era
  un espejismo, no una señal real sobre el subsistema que falla.
- `GT_tiene_cambios_de_gasto` se mantiene igual de limpio en ambos cortes (0% en exactos, ~17%
  en fallados) — es la señal más consistente entre las dos vistas, aunque también de base
  delgada (3 casos totales en el dataset).
- `n_series_recurrentes` y `n_categorias_flexibles` no discriminan: son casi universales en el
  dataset completo, exactos o no. **Se descartan como pista** — no ahorran tiempo si se
  persiguen.
- `tiene_moneda_extranjera`, `tiene_pending_credit`, `tiene_unrealized`: 1-2 casos en total en
  toda la muestra. No hay base estadística para afirmar ni descartar nada con esto.

## Nota de honestidad

n=6 exactos (n=3 en el control) es una muestra minúscula: cualquier proporción con esa base se
mueve mucho con un solo caso. Ninguna de estas tablas prueba causalidad — son proporciones sobre
25 filas. La pista más defendible con la evidencia de hoy es: **eventos `pending`/`scheduled`
dentro del horizonte de 90 días** (afecta a `finance/`, y probablemente explica parte de la
clase "rounding" del verificador — 5 de los 8 fallos "rounding" con SAFE fuera de ±1% tienen
`tiene_pending_debit>0`, contra 0 de los exactos-control) y **spending changes no resueltos**
(afecta a `decision/`). Ninguna de las dos se afirma como causa — sólo como dónde mirar primero.
