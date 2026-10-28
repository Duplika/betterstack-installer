# Semántica de los resúmenes

Las consultas de esta carpeta documentan los resúmenes corregidos sin incluir fuentes, IDs, perfiles o inventarios reales. Definir `source` como variable de fuente y `servidor` como lista dinámica múltiple basada en `label('host')`. Las consultas aceptan el filtro vacío.

## Último bloque no equivale a último ciclo

Better Stack puede consultar colecciones agregadas según el rango elegido. `argMax(avgMerge(value_avg), dt)` devuelve la media del último bloque, no necesariamente el último valor del agente. El timestamp del bloque tampoco es el instante exacto de recepción.

- Cobertura binaria: mínimo del último bloque; cero indica que hubo al menos un ciclo parcial.
- Fallos: máximo del último bloque; conserva un fallo aunque se haya recuperado dentro del bloque.
- Cuentas/LVEs: máximo del bloque, etiquetado como tal; no recuentos fraccionarios.
- CPU y RAM: medias del último bloque, explícitamente identificadas.
- Fracción histórica de ciclos completos: promedio ponderado por muestras, `sum(v * n) / sum(n)`.
- Recuentos del período procedentes de gauges por ciclo: `sum(v * n)`. No aplicar este método a counters acumulativos.
- Ausencia de métricas: desconocido, nunca cero o recuperación inventados.

Los resúmenes recientes usan los 15 minutos anteriores al final seleccionado. El selector mantiene su sentido histórico: al elegir un período pasado, se evalúa su final, no el reloj actual. La columna de edad es edad del bloque. En rangos mayores a 30 minutos se indica resumen agregado incluso si el último bloque parcial contiene una sola muestra; su edad no dispara por sí sola una conclusión de pérdida de señal. Para mayor detalle usar los últimos 30 minutos; no afirmar precisión de un ciclo si contiene varias muestras.

El resumen de hosting busca host y colector por separado. No exige que sus timestamps caigan en el mismo minuto ni descarta un host sólo porque falte una de las señales. El inventario de referencia toma hosts vistos en 30 días: no sustituye un inventario externo y un host ausente durante más de ese período dejaría de aparecer.

## Consultas

| Resumen | Archivo |
| --- | --- |
| Calidad reciente e historial | [hosting-quality.sql](dashboards/hosting-quality.sql) |
| Hosting: bloques recientes y frescura | [hosting-summary.sql](dashboards/hosting-summary.sql) |
| Atención por servidor | [fleet-summary.sql](dashboards/fleet-summary.sql) |
| Salud del colector | [collector-health.sql](dashboards/collector-health.sql) |

Estas consultas no cambian las seis reglas de alerta ni su semántica de confirmación/recuperación. Los gráficos históricos de tendencias siguen usando el rango elegido.

## Qué no certifican los resúmenes

Una cobertura mínima cero no indica cuántas solicitudes se perdieron. Un bloque agregado no prueba el estado exacto actual. Una identidad nueva no instala capacidades en un pipeline heredado. SMART no disponible no significa disco sano. La salud del colector Python excluye Vector, la sonda y la identidad salvo que se midan por separado.


## Compatibilidad con essential-v1

La marca duplika_monitoring_policy_essential cambia la interpretación de las series retiradas. Los accesos y el muestreo LVE por cuenta no se consideran obligatorios. La tabla de salud muestra el fallo/edad de la consulta histórica y si hay diagnóstico activo. Los recuentos cPanel se buscan en una ventana de 13 horas para cubrir su cadencia de 12 horas. Los datos rutinarios anteriores permanecen disponibles en el historial; no se representan como consumo actual ni se rellenan con ceros.

Los logs account_resource_faults son la fuente de las cuentas que requieren investigación; las métricas sólo muestran salud y recuentos globales. Consultar esos logs por event_kind, user y ventana. No contar filas como incidentes ni sumar ventanas repetidas tras un corte: deduplicar por host, window_id y recurso.
