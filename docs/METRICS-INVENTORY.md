# Inventario de medición y cadencias

Referencia: código observer-v4 rc26 fijado en el commit 777e541b43b2a625d966d6f66b4adbe42636b32b. Los pipelines heredados conservan su transporte y capacidades; no asumir que tienen este inventario por mostrar una identidad nueva. Los nombres de host_metrics disponibles dependen del sistema y dispositivos. Los intervalos son objetivos, sujetos a duración, timers, fallos y entrega.

## Medición actual

| Grupo | Qué se mide o lee | Consulta actual | Qué se envía |
| --- | --- | --- | --- |
| CPU de host | Tiempo acumulado por modo/CPU, para derivar utilización | 60 s, Vector | Métricas por muestra |
| RAM/swap | Total, disponible, libre/usada, activa, caché, buffers, compartida; swap/paginación cuando se exponen | 60 s, Vector | Métricas por muestra |
| Carga | load1/load5/load15 | 60 s, Vector | Métricas por muestra |
| Datos del host | Uptime, fecha de arranque, CPUs lógicas/físicas | 60 s, Vector | Métricas por muestra |
| I/O de disco | Bytes leídos/escritos y operaciones según dispositivo/SO | 60 s, Vector | Contadores para derivar tasas |
| Red | Bytes, paquetes, errores y descartes disponibles, por interfaz | 60 s, Vector | Contadores para derivar tasas |
| Filesystem Vector | Total/usado/libre, ocupación, inodos libres/usados/totales y proporción, por montaje visible | 60 s, Vector | Métricas; posibles aliases/sandbox |
| Volúmenes reales | Total/usado/disponible, proporción, sólo lectura; inodos cuando son aplicables y probe_ok | 60 s, sonda | Métricas por volumen real |
| Linux MD RAID | Activo, miembros esperados/presentes, degradado, sincronización | 60 s, sonda | Métricas por array |
| SMART | Aprobación, temperatura, sectores reasignados/pendientes/no corregibles; NVMe desgaste, spare, avisos y errores disponibles | Por disco, hasta una consulta cada 6 h; una por ejecución de sonda | Resultados sólo cuando se consulta; disponibilidad/edad por minuto |
| Procesos por usuario | CPU estimada, RSS, cantidad de procesos/bloqueados, bytes/s de lectura/escritura; procesos globales corriendo/bloqueados | 300 s | Métricas por usuario y totales |
| LVE de CloudLinux | CPU, RAM, IO bytes/s, IOPS, entry processes y cantidad de procesos | 300 s, cloudlinux-top | Métricas por cuenta observada |
| Límites LVE | Cociente uso/límite de los seis recursos disponibles | Con consulta LVE, 300 s | Métricas por cuenta/recurso; logs ante cambios/presión |
| MySQL Governor | CPU e I/O por cuenta | Con consulta LVE, 300 s, si Governor está habilitado | Métricas por cuenta |
| Dominios aislados CloudLinux | Los recursos disponibles de cada dominio que devuelve cloudlinux-top con --domains | Con consulta LVE, 300 s, si la capacidad está habilitada | Métricas por cuenta/dominio |
| Accesos HTTP por sitio | Archivos canónicos de dominios reconocidos: solicitudes, dinámicas, 4xx, 5xx, bytes y desglose local de rutas/UA/status | 60 s | Cinco familias de tasas por sitio; logs sólo para incidentes 5xx |
| Errores web globales | error_log y stderr.log globales disponibles; categorías LSAPI abort, memoria, crash, límites de workers, timeout, fatal/uncaught | 60 s, incremental | Resúmenes de coincidencias; no todos los renglones |
| Kernel | journalctl -k: OOM, crashes nativos y determinadas señales I/O/bloqueos | 60 s, con cursor | Eventos seleccionados |
| WordPress explícito | error_log del docroot y wp-content/debug.log existentes, sólo para selección temporal del perfil | 60 s si se habilitó; no es rastreo de todos los sitios | Conteos fatal/memoria/timeout y calidad, sin texto crudo |
| Inventario cPanel | Mapa de dominios/cuentas y usuarios para atribución; cuentas/dominios totales | Cada ciclo del colector, 60 s | Dos recuentos por minuto, no inventario completo rutinario |
| Salud del colector | Heartbeat, componentes fallidos, duración, CPU Python/con hijos, RSS Python y máximo individual de hijos | 60 s | Métricas; logs de cambios/fallos acotados |
| Calidad de acceso | Bytes leídos/omitidos, máximo backlog por archivo, líneas no interpretadas y cobertura completa | Con lectura de accesos, 60 s | Métricas por host; eventos de cobertura con política |
| Salud de procesos/LVE | Edad de última consulta exitosa, intervalo y fallo de cada componente | 60 s | Calidad por minuto; no reenvía recursos viejos cada minuto |
| Salud de almacenamiento | CPU con hijos, duración, RSS, fallos, cantidad de dispositivos/arrays/volúmenes | 60 s | Métricas; estado estructurado ante cambios o diario |
| Identidad/integridad/mantenimiento | Versiones/capacidades, hashes de código/configuración del monitor, servicios y salud; recomendación needs-restarting -r | Aproximadamente 1 h; también instalación/verificación según operación | Identidad inicial, ante cambios y diario; no envío completo horario si no cambió |

### Nombres principales del agente

- Procesos: duplika_account_cpu_percent, rss_bytes, process_count, blocked_processes, read_bytes_per_second, write_bytes_per_second (prefijo duplika_account_ en todos); duplika_processes_blocked/running.
- LVE: duplika_lve_cpu_percent, memory_bytes, io_bytes_per_second, iops, entry_processes, processes (prefijo duplika_lve_); duplika_lve_limit_ratio y duplika_lve_accounts_observed.
- Governor: duplika_mysql_cpu_percent/io_bytes_per_second. Aislados: duplika_isolated_domain_ con los seis recursos cuando están disponibles.
- Sitios: duplika_site_requests_per_second, dynamic_requests_per_second, errors_4xx_per_second, errors_5xx_per_second, response_bytes_per_second (prefijo duplika_site_).
- Almacenamiento: duplika_storage_volume_*, duplika_storage_raid_*, duplika_storage_smart_* y salud de la sonda.
- Calidad/costo: duplika_collector_*, duplika_access_*, duplika_processes_sample_* y duplika_lve_sample_*.

LVE y procesos no son medidas idénticas: los procesos se estiman desde instantáneas de /proc y pueden perder procesos que terminan entre muestras. LVE usa la respuesta de CloudLinux; no equivale a atribución perfecta por sitio. Governor comparte esa consulta, no ejecuta una sonda SQL independiente. Los valores no disponibles permanecen desconocidos.

## Frecuencia de logs y límites

Consultar cada minuto no implica enviar una copia de todos los logs cada minuto. Se lee desde posiciones guardadas, con presupuestos y clasificación local. Los logs sanos repetitivos ya se suprimen.

- LVE/recursos: cambios de metadatos/límites, inicio/fin de presión y recordatorios cada 15 min si persiste; no resumen rutinario de cada cuenta.
- Detalle de procesos: sólo incidentes de CPU alta o procesos bloqueados; máximo un detalle por usuario/motivo cada 15 min. No exporta argumentos ni entorno de procesos.
- Tráfico 5xx: inicio, recuperación y recordatorio cada 15 min si persiste. La evaluación actual es por minuto.
- Cobertura: parcial al detectar, recordatorios cada 15 min y recuperación tras tres ciclos completos consecutivos. Esa regla debe adaptarse a la futura lectura horaria.
- Heartbeat como log: cambios relevantes y diario. El heartbeat métrico sí es por minuto.
- Sonda: evento estructurado de cambios y diario.
- Accesos: máximo 8 MiB por ejecución y 2 MiB por archivo, sin lectura inicial de historial.
- Error global: lectura de hasta 1 MiB por archivo/ejecución; sólo categorías reconocidas.
- Kernel: lectura con cursor y tope de 200 registros por ejecución; no cobertura ilimitada de todo journal.
- WordPress: hasta 10 sitios explícitos, presupuesto compartido de 1 MiB por ejecución y vencimiento.

Global no significa todos los logs del sistema: el observer común lee los errores web globales configurados y kernel. No incluye automáticamente todos los logs de mail, SSH, cron, MySQL u otros servicios. Los pipelines heredados pueden tener fuentes/filtros distintos.

## Política siguiente: acordado frente a propuesta

Acordado para la próxima candidata, aún no desplegado:

1. Lectura de errores globales/kernel por minuto y envío de los errores relevantes.
2. CPU/RAM/carga y señales críticas de almacenamiento/salud por minuto.
3. Lectura y métricas de accesos por sitio cada hora; no lectura residual de domlogs por minuto.
4. Quitar medición filesystem duplicada donde exista sonda completa y validada.
5. Sin ampliación de comprobaciones externas.

Recomendaciones para decidir después de revisar este inventario:

| Grupo | Recomendación |
| --- | --- |
| LVE y Governor | Mantener 5 min: atribuyen presión a cuentas con costo compartido |
| Procesos por usuario | Evaluar 15 min o diagnóstico temporal; conservar si se usa para usuarios/servicios fuera de LVE |
| I/O de disco y red | Evaluar 5 min salvo que se necesiten picos breves |
| Inventario cPanel | Publicar cada hora; cachear con invalidación por cambios, conservando atribución |
| Rutas/UA y logs WordPress | Diagnóstico temporal explícito; fuera del funcionamiento normal por minuto |
| SMART | Mantener 6 h, disponibilidad/edad separadas de resultado |

La lectura horaria necesita presupuesto de bytes/CPU propio: reutilizar sin más los topes de un minuto puede omitir tráfico. Menos ejecuciones no significa leer 60 veces menos bytes si se intenta conservar todos los accesos. No inventar totales completos a partir de muestras parciales. Adaptar cadencias, salud, recuperación de cobertura, alertas y dashboards como parte de la misma candidata.
