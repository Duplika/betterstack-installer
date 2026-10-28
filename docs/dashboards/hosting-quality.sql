WITH samples AS (
  SELECT
    dt,
    label('host') AS host,
    name,
    avgMerge(value_avg) AS v,
    minMerge(value_min) AS lo,
    maxMerge(value_max) AS hi,
    sum(metrics_count) AS n
  FROM {{source}}
  WHERE
    dt >= {{start_time}} AND dt < {{end_time}}
    [[ AND match(label('host'), {{servidor}}) ]]
    AND name IN ('duplika_access_coverage_complete', 'duplika_access_skipped_bytes',
      'duplika_access_parse_failures', 'duplika_collector_failed_components')
    AND metrics_count > 0
  GROUP BY dt, host, name
)
SELECT
  host AS Servidor,
  max(dt) AS Inicio_ultimo_bloque,
  dateDiff('second', max(dt), {{end_time}}) AS Edad_del_bloque_segundos,
  if(countIf(name = 'duplika_access_coverage_complete') > 0,
    argMaxIf(lo, dt, name = 'duplika_access_coverage_complete'), NULL) AS Cobertura_minima_ultimo_bloque,
  if(countIf(name = 'duplika_access_coverage_complete') > 0,
    argMaxIf(n, dt, name = 'duplika_access_coverage_complete'), NULL) AS Muestras_cobertura_ultimo_bloque,
  sumIf(v * n, name = 'duplika_access_coverage_complete') /
    nullIf(sumIf(n, name = 'duplika_access_coverage_complete'), 0) AS Fraccion_ciclos_completos,
  if(countIf(name = 'duplika_collector_failed_components') > 0,
    argMaxIf(hi, dt, name = 'duplika_collector_failed_components'), NULL) AS Fallos_maximos_ultimo_bloque,
  sumIf(v * n, name = 'duplika_access_skipped_bytes') AS Bytes_omitidos_periodo,
  sumIf(v * n, name = 'duplika_access_parse_failures') AS Lineas_no_interpretadas_periodo
FROM samples
GROUP BY host
ORDER BY Servidor
LIMIT 100
