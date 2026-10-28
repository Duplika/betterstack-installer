# Operación de la candidata rc27

## Alcance

Sólo la selección explícita `--essential-monitoring` sobre observer-v4 rc26 completo con sonda activa cambia la política. Las rutas heredadas permanecen fuera de esta migración. No se cambia retención, fuente, credencial, aplicaciones, paquetes, límites LVE/Governor, firewall o sistema operativo.

Preflight valida baseline/runtime oficial rc26, perfil/versiones, grafo original de Vector, señal de sonda reciente y volúmenes reales bajo el mismo sandbox de la sonda. Requiere un inventario no vacío y capacidad disponible/sólo lectura para todos los volúmenes reportados. No es inventario de montajes de red o discos sin montar. Ejecuta cloudlinux-statistics cuando LVE es aplicable y prueba el nuevo grafo con el Vector instalado. Publica sólo cantidades y costo de la consulta, sin nombres de clientes ni salida cruda.

El upgrade repite esas comprobaciones, respalda código/perfil/configuración/manifiesto y estados de los servicios afectados, pausa timers, espera el lock y verifica nuevamente el baseline. Conserva los buffers y cursores. El transporte se detiene brevemente al reemplazar el grafo y se valida como duplikaobs antes de arrancarlo. La sonda no se reemplaza. Se exige un ciclo del código nuevo con marcador essential-v1 antes de terminar la transacción. Ante fallo se restaura la última transacción sin retroceder cursores.

## Historial de presión

Se consulta una ventana UTC cerrada de 900 s, dejando 60 s para la escritura local de estadísticas. Se fijan --from/--to, --by-fault=any, --threshold=10 y --limit=0. No se combina --period, porque anularía las fechas explícitas. El adaptador requiere JSON de éxito con users y faults en el esquema documentado; no interpreta un error, timeout o esquema desconocido como cero cuentas.

El filtro final es por recurso: CPU, PMEM, IO, IOPS, EP y NPROC. Un recurso necesita estrictamente más de diez fallos. Seis CPU más seis PMEM no seleccionan la cuenta. No hay filtro por 90% de uso, sondeo SQL independiente ni métricas rutinarias por cuenta. El contexto MySQL disponible se adjunta a las cuentas que sí resultaron seleccionadas; eso no garantiza cobertura de restricciones Governor independientes.

Cada log `account_resource_faults` incluye user, recursos afectados con faults/usage_average/limit disponibles, window_start/window_end y window_id. No adjunta consultas SQL, mensajes crudos ni rutas. La semántica exacta de los contadores y de los endpoints de fechas debe validarse contra el CLI instalado. No equiparar fallos a peticiones, segundos o incidentes independientes.

Se confirma estado de ventana sólo después de una consulta satisfactoria. Un fallo mantiene el indicador de fallo y reintenta como máximo cada cinco minutos; no repite una ventana ya confirmada. Si faltaron ventanas se registra la brecha, sin backfill ilimitado. Una corrección hacia atrás del reloj no autoriza replay. Spool y estado no forman una transacción única: un corte entre ambos puede duplicar un evento; al sumar, deduplicar por host, window_id y recurso.

Se emiten cada minuto edad/intervalo/fallo de la consulta histórica; el recuento global de cuentas afectadas sólo al consultar. Not supported no es una cuenta con cero consumo: en Solo/AlmaLinux no se ejecuta el CLI ni se inventan datos LVE.

## Diagnóstico explícito temporal

Una única cuenta exacta por selección; lectores access/lve/processes/wordpress y vencimiento entre 1 y 60 minutos. Las órdenes del README son propuestas concretas para operar un servidor ya actualizado; no instalan paquetes ni amplían privilegios.

La selección vive en un JSON privado root 0600. El colector valida identidad, lectores y tiempos finitos, y deja de ejecutar lectores al vencer. Un salto de reloj invalida una selección temporal ambigua. Los cursores diagnósticos son separados de los cursores previos conservados para rollback. La primera lectura de accesos empieza en EOF, sin enviar historia. No se leen accesos de otros dominios; procesos y LVE filtran la exportación a esa cuenta.

Access conserva el parser y límites de lectura existentes, pero sólo para los dominios elegidos. Puede generar tráfico/5xx y calidad durante la investigación; todo ello desaparece del funcionamiento por defecto. Processes sigue necesitando recorrer procfs para atribuir datos, por eso sólo se usa temporalmente. WordPress requiere además selección y vencimiento del perfil, docroots ya incluidos en el sandbox y dominio perteneciente a la cuenta. No añade binds, no lee wp-config.php ni habilita WP_DEBUG. Si faltan esas condiciones, se rechaza la activación WordPress.

## Datos y cadencias

| Bloque | Cadencia esencial |
| --- | --- |
| CPU/carga/host | 60 s, mismos nombres |
| RAM | 60 s: total, disponible, swap total/usada y OOM si se expone |
| Red/disco I/O | Scrape separado de 300 s; no filtrado posterior de muestras de 60 s |
| Filesystem Vector | Retirado sólo después de comprobar la sonda |
| Volúmenes/inodos/RAID | Sonda existente de 60 s |
| SMART | Sin cambios, consulta hasta cada 6 h por disco |
| Errores web globales/kernel | Lectura cada 60 s y envío sólo de las categorías existentes |
| Cuentas | Historial 900 s, sólo logs de presión por recurso |
| Inventario | Dos métricas de recuento cada 43200 s; invalidación del mapa por device/inode/size/mtime_ns de userdomains y passwd |
| Salud | Heartbeat, fallos, duración, CPU con hijos y RSS Python de 60 s; marca de política y diagnóstico activo |
| Identidad/mantenimiento | Sin cambios: comprobación horaria, envío por cambios y diario |

Las métricas CPU/RSS del colector no representan simultáneamente todo el monitoreo: Vector, sonda e identidad se comparan por separado mediante cgroup. Los logs globales del observer siguen siendo error_log/stderr.log web configurados y kernel, no todos los logs del sistema. La política conserva sus presupuestos de lectura y categorías; no garantiza conservar todos los eventos bajo saturación.

## Aceptación del piloto

1. Guardar la salida de preflight: cobertura de volúmenes, schema/costo de consulta y Vector nativo.
2. Ejecutar upgrade explícito y verify con el mismo artefacto fijado/verificado.
3. Verificar en Better Stack marca de política, heartbeat/sonda y CPU/RAM frescos. Confirmar red/I/O cada cinco minutos y ausencia de muestras nuevas de filesystem o de cuentas/sitios rutinarios después del drenaje inicial.
4. Esperar al menos dos ventanas históricas completas: comprobar cuentas/recursos/umbrales contra CloudLinux, schema real y límites temporales sin duplicación de extremos.
5. Activar un diagnóstico corto de una cuenta explícita y verificar ámbito y vencimiento. No habilitar WordPress sin sandbox aprobado.
6. Comparar 24 horas equivalentes de CPU por cgroup, memoria, duración, logs por tipo y muestras por familia. Las estimaciones previas no son un resultado medido.
7. Revisar antes de ampliar por lotes. Los dashboards distinguen política esencial del muestreo anterior; ausencia deliberada de accesos/LVE por cuenta no es un fallo.

Si el preflight falla o una consulta resulta demasiado costosa bajo la cuota instalada, conservar rc26 y revisar la evidencia. No editar perfiles, hashes o baseline para forzar aceptación. No se recertifica un grafo desconocido.
