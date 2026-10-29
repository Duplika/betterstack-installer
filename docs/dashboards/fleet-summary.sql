WITH points AS (SELECT dt, label('host') AS host, name, label('mode') AS mode, multiIf(name='duplika_access_coverage_complete',minMerge(value_min),name IN ('duplika_collector_failed_components','duplika_storage_probe_failures'),maxMerge(value_max),avgMerge(value_avg)) AS v, avgMerge(rate_avg) AS r, sum(metrics_count) AS n FROM {{source}} WHERE dt >= greatest({{start_time}}, {{end_time}} - INTERVAL 15 MINUTE) AND dt < {{end_time}} [[ AND match(label('host'), {{servidor}}) ]] AND name IN ('uptime','logical_cpus','load5','memory_available_bytes','memory_total_bytes','memory_swap_used_bytes','memory_swap_total_bytes','cpu_seconds_total','duplika_collector_failed_components','duplika_access_coverage_complete','duplika_storage_probe_failures','duplika_monitoring_policy_essential') GROUP BY dt,host,name,mode), latest AS (
SELECT host,maxIf(dt,name='memory_total_bytes') AS last_at,
if(countIf(name='duplika_monitoring_policy_essential')>0,argMaxIf(v,dt,name='duplika_monitoring_policy_essential'),0) AS essential,
if(countIf(name='memory_total_bytes')>0,argMaxIf(n,dt,name='memory_total_bytes'),NULL) AS block_samples,
if(countIf(name = 'logical_cpus') > 0, argMaxIf(v, dt, name = 'logical_cpus'), NULL) AS cpus,if(countIf(name = 'load5') > 0, argMaxIf(v, dt, name = 'load5'), NULL) AS load,
if(countIf(name = 'memory_available_bytes') > 0, argMaxIf(v, dt, name = 'memory_available_bytes'), NULL) AS available,if(countIf(name = 'memory_total_bytes') > 0, argMaxIf(v, dt, name = 'memory_total_bytes'), NULL) AS total,
if(countIf(name = 'memory_swap_used_bytes') > 0, argMaxIf(v, dt, name = 'memory_swap_used_bytes'), NULL) AS swap_used,if(countIf(name = 'memory_swap_total_bytes') > 0, argMaxIf(v, dt, name = 'memory_swap_total_bytes'), NULL) AS swap_total,
if(countIf(name = 'uptime') > 0, argMaxIf(v, dt, name = 'uptime'), NULL) AS uptime,
if(countIf(name='cpu_seconds_total' AND mode='idle' AND isFinite(r))>0,1-argMaxIf(r,dt,name='cpu_seconds_total' AND mode='idle' AND isFinite(r)),NULL) AS cpu,
if(countIf(name='duplika_collector_failed_components')>0,argMaxIf(v,dt,name='duplika_collector_failed_components'),NULL) AS failures,
if(countIf(name='duplika_access_coverage_complete')>0,argMaxIf(v,dt,name='duplika_access_coverage_complete'),NULL) AS coverage,
if(countIf(name='duplika_storage_probe_failures')>0,argMaxIf(v,dt,name='duplika_storage_probe_failures'),NULL) AS storage_failed
FROM points GROUP BY host),
disks AS (WITH points AS (
SELECT dt,label('host') AS host,label('device') AS device,label('mountpoint') AS mount,name,avgMerge(value_avg) AS v
FROM {{source}} WHERE dt >= greatest({{start_time}}, {{end_time}} - INTERVAL 15 MINUTE) AND dt < {{end_time}} [[ AND match(label('host'), {{servidor}}) ]]
AND name IN ('duplika_storage_volume_used_ratio','duplika_storage_volume_inodes_used_ratio','duplika_storage_volume_available_bytes','duplika_storage_volume_readonly','filesystem_used_ratio','filesystem_inodes_used_ratio','filesystem_free_bytes')
AND startsWith(label('device'),'/dev/') GROUP BY dt,host,device,mount,name
), mounts AS (
SELECT host,device,mount,max(dt) AS at,
countIf(startsWith(name,'duplika_storage_'))>0 AS dedicated,
if(countIf(name='duplika_storage_volume_used_ratio')>0,if(countIf(name = 'duplika_storage_volume_used_ratio') > 0, argMaxIf(v, dt, name = 'duplika_storage_volume_used_ratio'), NULL),if(countIf(name = 'filesystem_used_ratio') > 0, argMaxIf(v, dt, name = 'filesystem_used_ratio'), NULL)) AS used,
if(countIf(startsWith(name,'duplika_storage_'))>0,if(countIf(name = 'duplika_storage_volume_inodes_used_ratio') > 0, argMaxIf(v, dt, name = 'duplika_storage_volume_inodes_used_ratio'), NULL),if(countIf(name = 'filesystem_inodes_used_ratio') > 0, argMaxIf(v, dt, name = 'filesystem_inodes_used_ratio'), NULL)) AS inodes,
if(countIf(name='duplika_storage_volume_available_bytes')>0,if(countIf(name = 'duplika_storage_volume_available_bytes') > 0, argMaxIf(v, dt, name = 'duplika_storage_volume_available_bytes'), NULL),if(countIf(name = 'filesystem_free_bytes') > 0, argMaxIf(v, dt, name = 'filesystem_free_bytes'), NULL)) AS free,
if(countIf(name = 'duplika_storage_volume_readonly') > 0, argMaxIf(v, dt, name = 'duplika_storage_volume_readonly'), NULL) AS readonly
FROM points GROUP BY host,device,mount
), chosen AS (
SELECT host,device,argMin(tuple(mount,at,used,inodes,free,readonly,dedicated),
tuple(if(dedicated,0,1),multiIf(mount='/',0,mount='/home',1,mount='/var/lib/mysql',2,mount='/backup',3,mount='/tmp',4,startsWith(mount,'/usr/share/cagefs'),9,5),length(mount))) AS x
FROM mounts GROUP BY host,device)
SELECT host AS Servidor,x.1 AS Volumen,device AS Dispositivo,x.3 AS Espacio_usado,
if(isFinite(x.4),x.4,NULL) AS Inodos_usados,x.5 AS Bytes_disponibles,x.6 AS Solo_lectura,
if(x.7,'Sonda real','Vector heredado') AS Medicion,x.2 AS Ultima_muestra
FROM chosen),
worst AS (SELECT Servidor,max(Espacio_usado) AS disk_used,argMax(Volumen,Espacio_usado) AS disk_mount,max(Inodos_usados) AS inode_used,max(Solo_lectura) AS readonly FROM disks GROUP BY Servidor),
base AS (
 SELECT e.Servidor AS Servidor,l.last_at,l.essential,l.block_samples,l.cpu,l.failures,l.coverage,l.storage_failed,1-l.available/nullIf(l.total,0) AS ram,l.load/nullIf(l.cpus,0) AS load,d.disk_used,d.disk_mount,d.inode_used,d.readonly,
 if(l.last_at IS NOT NULL,dateDiff('second',l.last_at,{{end_time}}),NULL) AS age
 FROM (SELECT DISTINCT label('host') AS Servidor FROM {{source}}
WHERE dt >= {{end_time}} - INTERVAL 30 DAY AND dt < {{end_time}}
AND name='memory_total_bytes' AND metrics_count>0) e
 LEFT JOIN latest l ON e.Servidor=l.host LEFT JOIN worst d ON e.Servidor=d.Servidor
 [[ WHERE match(e.Servidor, {{servidor}}) ]]
), scored AS (
SELECT *,
multiIf(last_at IS NULL OR (age>300 AND dateDiff('second',{{start_time}},{{end_time}})<=1800),3,cpu IS NULL OR ram IS NULL,2,
 readonly>0 OR failures>0 OR storage_failed>0 OR cpu>=0.9 OR ram>=0.9 OR load>=1.5 OR disk_used>=0.9 OR inode_used>=0.9,2,
 (coalesce(essential,0)=0 AND coverage<1) OR disk_used IS NULL OR cpu>=0.8 OR ram>=0.85 OR load>=1 OR disk_used>=0.8 OR inode_used>=0.8,1,0) AS priority
FROM base)
SELECT Servidor,if(essential=1,'Esencial','Anterior') AS Modo_monitoreo,multiIf(priority=3,if(dateDiff('second',{{start_time}},{{end_time}})>1800,'Sin bloque reciente · usar 30 min','Sin señal reciente'),priority=2,'Revisar',priority=1,'Vigilar',dateDiff('second',{{start_time}},{{end_time}})>1800 OR block_samples>1,'Resumen agregado','Normal') AS Estado,
arrayStringConcat(arrayFilter(x->x!='',[
if(last_at IS NULL OR (age>300 AND dateDiff('second',{{start_time}},{{end_time}})<=1800),'Sin bloque reciente; comprobar señal en rango corto',''),
if(cpu IS NULL OR ram IS NULL,'Datos de recursos incompletos',''),
if(dateDiff('second',{{start_time}},{{end_time}})>1800 OR block_samples>1,'Bloque agregado: usar 30 min para mayor detalle',''),
if(cpu>=0.8,'CPU alta',''),if(ram>=0.85,'Memoria alta',''),
if(load>=1,'Carga elevada',''),if(disk_used>=0.8,concat('Espacio en ',disk_mount),''),
if(readonly>0,'Volumen en solo lectura',''),if(failures>0,'Fallo del recolector',''),if(storage_failed>0,'Fallo al medir almacenamiento',''),if((coalesce(essential,0)=0 AND coverage<1),'Lectura de accesos parcial',''),if(inode_used>=0.8,'Inodos elevados',''),if(disk_used IS NULL,'Sin dato de disco','')]),' · ') AS Revisar,
cpu AS CPU_media_bloque,ram AS RAM_media_bloque,load AS Carga_por_CPU,disk_used AS Disco_maximo,inode_used AS Inodos_maximos,age AS Edad_del_bloque_segundos,block_samples AS Muestras_host_ultimo_bloque
FROM scored ORDER BY priority DESC,Disco_maximo DESC,Servidor SETTINGS join_use_nulls=1
