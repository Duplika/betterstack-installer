WITH points AS (
  SELECT
    dt,
    label('host') AS host,
    name,
    label('php') AS php,
    label('status') AS status,
    label('installed') AS installed,
    label('installed_build') AS installed_build,
    label('available') AS available,
    label('available_build') AS available_build,
    maxMerge(value_max) AS state
  FROM {{source}}
  WHERE dt >= {{end_time}} - INTERVAL 7 DAY AND dt < {{end_time}}
    AND name IN ('duplika_litespeed_update_state', 'duplika_timezonedb_update_state', 'duplika_timezonedb_php_update_state', 'duplika_reboot_recommended')
    AND metrics_count > 0
    [[ AND match(label('host'), {{servidor}}) ]]
  GROUP BY dt, host, name, php, status, installed, installed_build, available, available_build
), latest AS (
  SELECT host, name, php,
    argMax(tuple(status, installed, installed_build, available, available_build, state), dt) AS x,
    max(dt) AS checked
  FROM points
  GROUP BY host, name, php
)
SELECT
  host AS Servidor,
  multiIf(name='duplika_reboot_recommended', 'Sistema · reinicio',
          name='duplika_litespeed_update_state', 'LiteSpeed',
          name='duplika_timezonedb_php_update_state', concat('timezoneDB · PHP ', substring(php, 1, 1), '.', substring(php, 2)),
          'timezoneDB · resumen') AS Componente,
  multiIf(dateDiff('second', checked, {{end_time}}) > 129600, 'Comprobación vencida',
    x.1='indicated', 'Reinicio recomendado',
    x.1='version_available', 'Versión nueva disponible',
    x.1 IN ('build_available', 'update_available'), 'Actualización disponible',
    x.1='missing', 'Extensión faltante o deshabilitada',
    x.1 IN ('unknown', 'not_checked'), 'No se pudo comprobar',
    x.1='ahead_of_stable', 'Instalado por delante de STABLE', 'Actualizado') AS Pendiente,
  concat(x.2, if(x.3='', '', concat(' build ', x.3))) AS Instalado,
  concat(x.4, if(x.5='', '', concat(' build ', x.5))) AS Disponible,
  checked AS Ultimo_chequeo
FROM latest
WHERE x.6 != -2
  AND (x.6 != 0 OR dateDiff('second', checked, {{end_time}}) > 129600)
  AND (name != 'duplika_timezonedb_update_state' OR
       host NOT IN (SELECT host FROM latest WHERE name='duplika_timezonedb_php_update_state'))
ORDER BY checked ASC, Servidor, Componente
LIMIT 250
