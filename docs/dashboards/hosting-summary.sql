WITH points AS (
  SELECT
    dt,
    label('host') AS host,
    name,
    label('mode') AS mode,
    avgMerge(value_avg) AS v,
    minMerge(value_min) AS lo,
    maxMerge(value_max) AS hi,
    avgMerge(rate_avg) AS r,
    sum(metrics_count) AS n
  FROM {{source}}
  WHERE
    dt >= {{end_time}} - INTERVAL 15 MINUTE AND dt < {{end_time}}
    [[ AND match(label('host'), {{servidor}}) ]]
    AND name IN ('duplika_collector_heartbeat', 'duplika_cpanel_accounts',
      'duplika_lve_accounts_observed', 'duplika_monitoring_policy_essential', 'cpu_seconds_total', 'memory_available_bytes',
      'memory_total_bytes', 'duplika_collector_failed_components', 'duplika_access_coverage_complete')
    AND metrics_count > 0
  GROUP BY dt, host, name, mode
), latest AS (
  SELECT
    host,
    if(countIf(name = 'duplika_monitoring_policy_essential') > 0, argMaxIf(v, dt, name = 'duplika_monitoring_policy_essential'), 0) AS essential,
    if(countIf(name = 'duplika_collector_heartbeat') > 0, maxIf(dt, name = 'duplika_collector_heartbeat'), NULL) AS collector_at,
    if(countIf(name = 'memory_total_bytes') > 0, maxIf(dt, name = 'memory_total_bytes'), NULL) AS host_at,
    if(countIf(name = 'duplika_collector_heartbeat') > 0, argMaxIf(n, dt, name = 'duplika_collector_heartbeat'), NULL) AS collector_samples,
    if(countIf(name = 'memory_total_bytes') > 0, argMaxIf(n, dt, name = 'memory_total_bytes'), NULL) AS host_samples,
    if(countIf(name = 'duplika_cpanel_accounts') > 0, argMaxIf(hi, dt, name = 'duplika_cpanel_accounts'), NULL) AS accounts,
    if(countIf(name = 'duplika_lve_accounts_observed') > 0, argMaxIf(hi, dt, name = 'duplika_lve_accounts_observed'), NULL) AS observed,
    if(countIf(name = 'duplika_lve_accounts_observed') > 0, maxIf(dt, name = 'duplika_lve_accounts_observed'), NULL) AS lve_at,
    if(countIf(name = 'cpu_seconds_total' AND mode = 'idle' AND isFinite(r)) > 0,
      1 - argMaxIf(r, dt, name = 'cpu_seconds_total' AND mode = 'idle' AND isFinite(r)), NULL) AS cpu,
    if(countIf(name = 'memory_available_bytes') > 0, argMaxIf(v, dt, name = 'memory_available_bytes'), NULL) AS available,
    if(countIf(name = 'memory_total_bytes') > 0, argMaxIf(v, dt, name = 'memory_total_bytes'), NULL) AS total,
    if(countIf(name = 'duplika_collector_failed_components') > 0, argMaxIf(hi, dt, name = 'duplika_collector_failed_components'), NULL) AS failures,
    if(countIf(name = 'duplika_access_coverage_complete') > 0, argMaxIf(lo, dt, name = 'duplika_access_coverage_complete'), NULL) AS coverage
  FROM points
  GROUP BY host
), inventory AS (
  SELECT host, argMax(v, dt) AS accounts
  FROM (
    SELECT dt, label('host') AS host, maxMerge(value_max) AS v
    FROM {{source}}
    WHERE dt >= {{end_time}} - INTERVAL 13 HOUR AND dt < {{end_time}}
      AND name = 'duplika_cpanel_accounts' AND metrics_count > 0
      [[ AND match(label('host'), {{servidor}}) ]]
    GROUP BY dt, host
  )
  GROUP BY host
), expected AS (
  SELECT DISTINCT label('host') AS host
  FROM {{source}}
  WHERE
    dt >= {{end_time}} - INTERVAL 30 DAY AND dt < {{end_time}}
    AND name = 'memory_total_bytes' AND metrics_count > 0
    [[ AND match(label('host'), {{servidor}}) ]]
)
SELECT
  e.host AS Servidor,
  multiIf(l.collector_at IS NULL OR l.host_at IS NULL, 'Sin bloque reciente',
    dateDiff('second', {{start_time}}, {{end_time}}) > 1800, 'Resumen agregado',
    l.collector_samples > 1 OR l.host_samples > 1, 'Bloque agregado',
    dateDiff('second', l.collector_at, {{end_time}}) > 300, 'Revisar frescura',
    'Muestra reciente') AS Estado_datos,
  l.collector_at AS Inicio_bloque_colector,
  dateDiff('second', l.collector_at, {{end_time}}) AS Edad_bloque_colector_segundos,
  l.collector_samples AS Muestras_colector_en_bloque,
  l.host_at AS Inicio_bloque_host,
  l.host_samples AS Muestras_host_en_bloque,
  inv.accounts AS Cuentas_ultimo_inventario,
  if(l.essential=1,NULL,l.observed) AS LVEs_observados_max_bloque,
  l.lve_at AS Inicio_bloque_LVE,
  l.cpu AS CPU_media_bloque,
  1 - l.available / nullIf(l.total, 0) AS RAM_media_bloque,
  l.failures AS Componentes_fallidos_max_bloque,
  if(l.essential=1,NULL,l.coverage) AS Cobertura_minima_bloque,
  if(l.essential=1,'Sólo fallos >10 por recurso / 15 min','Muestreo anterior') AS Cuentas_supervisadas
FROM expected e
LEFT JOIN latest l ON e.host = l.host
LEFT JOIN inventory inv ON e.host = inv.host
ORDER BY Servidor
LIMIT 100
SETTINGS join_use_nulls = 1
