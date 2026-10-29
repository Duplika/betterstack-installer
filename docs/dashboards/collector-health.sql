WITH points AS (
  SELECT
    dt, label('host') AS host, name,
    if(name='duplika_access_coverage_complete',minMerge(value_min),maxMerge(value_max)) AS v
  FROM {{source}}
  WHERE dt >= {{end_time}} - INTERVAL 15 MINUTE AND dt < {{end_time}}
    [[ AND match(label('host'), {{servidor}}) ]]
    AND name IN ('duplika_collector_heartbeat','duplika_collector_failed_components',
      'duplika_access_coverage_complete','duplika_lve_sample_failed','duplika_lve_accounts_observed',
      'duplika_lve_sample_age_seconds','duplika_storage_probe_failures',
      'duplika_monitoring_policy_essential','duplika_monitoring_diagnostic_active',
      'duplika_lve_history_sample_failed','duplika_lve_history_sample_age_seconds',
      'duplika_disk_io_sample_failed','duplika_disk_io_sample_age_seconds','duplika_disk_io_devices_observed')
    AND metrics_count > 0
  GROUP BY dt, host, name
), latest AS (
  SELECT host, max(dt) AS at,
    countIf(name='duplika_monitoring_policy_essential')>0 AS essential,
    if(countIf(name='duplika_collector_failed_components')>0,argMaxIf(v,dt,name='duplika_collector_failed_components'),NULL) AS failures,
    if(countIf(name='duplika_access_coverage_complete')>0,argMaxIf(v,dt,name='duplika_access_coverage_complete'),NULL) AS coverage,
    if(countIf(name='duplika_lve_sample_failed')>0,argMaxIf(v,dt,name='duplika_lve_sample_failed'),NULL) AS lve_failed,
    if(countIf(name='duplika_lve_sample_age_seconds')>0,argMaxIf(v,dt,name='duplika_lve_sample_age_seconds'),NULL) AS lve_age,
    if(countIf(name='duplika_lve_accounts_observed')>0,argMaxIf(v,dt,name='duplika_lve_accounts_observed'),NULL) AS observed,
    if(countIf(name='duplika_lve_accounts_observed')>0,maxIf(dt,name='duplika_lve_accounts_observed'),NULL) AS lve_at,
    if(countIf(name='duplika_storage_probe_failures')>0,argMaxIf(v,dt,name='duplika_storage_probe_failures'),NULL) AS storage_failed,
    if(countIf(name='duplika_lve_history_sample_failed')>0,argMaxIf(v,dt,name='duplika_lve_history_sample_failed'),NULL) AS history_failed,
    if(countIf(name='duplika_lve_history_sample_age_seconds')>0,argMaxIf(v,dt,name='duplika_lve_history_sample_age_seconds'),NULL) AS history_age,
    if(countIf(name='duplika_monitoring_diagnostic_active')>0,argMaxIf(v,dt,name='duplika_monitoring_diagnostic_active'),NULL) AS diagnostic,
    if(countIf(name='duplika_disk_io_sample_failed')>0,argMaxIf(v,dt,name='duplika_disk_io_sample_failed'),NULL) AS disk_io_failed,
    if(countIf(name='duplika_disk_io_sample_age_seconds')>0,argMaxIf(v,dt,name='duplika_disk_io_sample_age_seconds'),NULL) AS disk_io_age,
    if(countIf(name='duplika_disk_io_devices_observed')>0,argMaxIf(v,dt,name='duplika_disk_io_devices_observed'),NULL) AS disk_io_devices
  FROM points
  GROUP BY host
), expected AS (
  SELECT DISTINCT label('host') AS host
  FROM {{source}}
  WHERE dt >= {{end_time}} - INTERVAL 30 DAY AND dt < {{end_time}}
    AND name='memory_total_bytes' AND metrics_count>0
    [[ AND match(label('host'), {{servidor}}) ]]
)
SELECT
  e.host AS Servidor,
  l.at AS Inicio_ultimo_bloque,
  dateDiff('second',l.at,{{end_time}}) AS Edad_del_bloque_segundos,
  l.failures AS Componentes_fallidos_max_bloque,
  if(l.essential=1,NULL,l.coverage) AS Cobertura_minima_bloque,
  if(l.essential=1,NULL,l.lve_failed) AS Fallo_LVE_max_bloque,
  if(l.essential=1,NULL,l.observed) AS LVEs_observados_max_bloque,
  if(l.essential=1,NULL,l.lve_at) AS Inicio_bloque_LVE,
  if(l.essential=1 OR l.lve_at IS NULL,NULL,l.lve_age) AS Edad_LVE_max_bloque_segundos,
  l.storage_failed AS Fallos_almacenamiento_max_bloque,
  if(l.essential=1,'Historial 15 min; detalle sólo en diagnóstico','Muestreo anterior') AS Modo_monitoreo,
  l.history_failed AS Fallo_consulta_historica,
  l.history_age AS Edad_consulta_historica_segundos,
  l.diagnostic AS Diagnostico_temporal_activo,
  l.disk_io_failed AS Fallo_lectura_IO,
  l.disk_io_age AS Edad_muestra_IO_segundos,
  l.disk_io_devices AS Dispositivos_IO_observados
FROM expected e
LEFT JOIN latest l ON e.host=l.host
ORDER BY Servidor
SETTINGS join_use_nulls=1
