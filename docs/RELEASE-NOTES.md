# 4.0.0-rc30 — candidata: muestreo de host liviano sin perder errores WordPress

Agrega una selección explícita --essential-monitoring para los dos layouts heredados AlmaLinux WordPress revisados. Conserva lectores incrementales, filtros/agregaciones WordPress/PHP/WooCommerce, fuente, credencial, perfil, unidades, usuario, data_dir, buffers y offsets. CPU/RAM esencial/carga/host/filesystem siguen a 60 s; disk/network pasan a 300 s. No instala sonda root/SMART, ejecuta WordPress ni consulta SQL; la política se identifica como essential-host-v1.

Preflight compara el blueprint oficial y valida el nuevo grafo con Vector nativo. Una sonda temporal local de host, sin credenciales ni destino remoto, exige filesystem, red y cuatro contadores válidos por disco como usuario vector. No reproduce el sandbox completo de systemd ni acredita entrega remota. El upgrade drena y reinicia sólo el emisor existente, valida como vector y revierte archivos ante fallo sin retroceder cursores. Bloquea un segundo emisor activo y usuarios no revisados.

Incorpora la base rc29 congelada para conservar la ruta observer-v4 y las correcciones de quiescencia/rollback de rc29. No cambia las referencias legacy ni los artefactos publicados. Las pruebas cubren ambos grafos nativos, conservación de lectores y destinos, configuración alterada, emisor duplicado, cobertura incompleta, idempotencia y rollback con cursores avanzados. La aceptación requiere el servicio real, recepción remota, dos muestras lentas y comparación de costo durante 24 h; no se declara aceptación del piloto con pruebas locales.

# 4.0.0-rc29 — candidata: esperar ciclos encolados y respetar el plazo de parada

Corrige una carrera del upgrade: detener un timer no garantiza que el servicio anterior haya terminado de arrancar. Tomar collector.lock primero podía provocar EAGAIN en ese ciclo y abortar con previous_observer_job_failed. Las rutas esencial y observer-v4 esperan al servicio antes del bloqueo y de reemplazar archivos; conservan también la comprobación posterior y rechazan fallos reales sin ocultarlos.

El rollback usaba el plazo de probes de 20 s para detener una unidad que permite hasta 30 s. Ahora usa 120 s, igual que las operaciones de servicio del instalador; comprueba códigos de salida y no trata como fallo la ausencia esperada de una unidad nueva. Conserva el error original si la recuperación realmente falla y no retrocede cursores ni buffers.

Agrega la base congelada rc28 a las migraciones revisadas, con hashes extraídos de su artefacto inmutable. El grafo, los privilegios, las unidades, las frecuencias y el protocolo remoto no cambian. Las pruebas reproducen EAGAIN ejecutando main() del colector rc26 congelado con un bloqueo real y ejercitan un ciclo encolado durante la transacción. La candidata aún requiere publicación y aceptación nativa/remota; las pruebas locales no certifican el estado de ningún servidor.

# 4.0.0-rc28 — candidata: I/O privilegiado y salud sin falsos heartbeats inactivos

La migración explícita --essential-monitoring acepta las bases oficiales rc26 observer-v4 y rc27 esencial con sonda. Conserva los artefactos anteriores, fuente, credencial, buffers, offsets y unidades del transporte/observador. El colector root ya existente lee I/O cada 300 s y emite los cuatro contadores disk_* de Vector 0.60; Vector conserva red cada 300 s y deja de leer disk. No cambia permisos del kernel, capacidades ni el usuario del transporte. Preflight exige muestras nativas del parser y el primer ciclo real instalado debe confirmar I/O; cualquier fallo revierte sin retroceder cursores. Un segundo vector.service activo bloquea la migración y nunca se inicia automáticamente.

Identidad separa servicios, liveness y componentes degradados. El heartbeat válido no desaparece cuando LVE falla. status global, marcador local y métrica de fallos son coherentes; se conservan los datos de componentes sanos. El marcador incluye boot_id para no aceptar señal del arranque anterior. Los fallos de comandos conservan códigos y categorías fijas, sin salida cruda ni argumentos privados. Se mantiene diagnóstico temporal y el resto de frecuencias esenciales.

Pruebas sintéticas cubren fallos/permisos/recuperación, reinicio, no replay, ventanas, migración de ambas bases y rollback conservando cursores. La candidata requiere aceptación nativa/remota del piloto; no se considera desplegada ni aceptada por aprobar las pruebas locales.

# 4.0.0-rc27 — candidata: monitoreo esencial explícito desde rc26

Incorpora --essential-monitoring para preflight/upgrade de observer-v4 rc26 con almacenamiento validado. No modifica los artefactos anteriores ni referencias legacy. La opción retira filesystem de Vector, separa red/I/O a 300 s y filtra memoria sin consumidores. Mantiene CPU/RAM/carga y salud global cada 60 s.

El ciclo esencial no invoca accesos, procesos por usuario, cloudlinux-top o lectores WordPress de forma rutinaria. Reemplaza el muestreo general de cuentas por una consulta histórica cerrada de 900 s con un minuto de margen. El filtro remoto por fallos se verifica nuevamente por recurso con umbral estricto mayor que 10; no suma fallos distintos ni selecciona por porcentaje. Sólo exporta logs de cuentas afectadas y salud global. El inventario emite recuentos cada 43200 s y cachea el mapa con invalidación por metadatos.

Los diagnósticos de cuenta exacta tienen lectores explícitos y vencimiento máximo 60 min. WordPress requiere rutas/sandbox previamente aprobados; no abre nuevos docroots. La migración valida schema histórico, cobertura real de volúmenes y Vector nativo, conserva transporte/cursores, exige un ciclo nuevo con marcador de política y revierte ante fallo. Los endpoints temporales, disponibilidad/semántica de fault counters y costo real deben verificarse en el servidor antes de aceptar el piloto.

Los dashboards distinguen monitoreo esencial del anterior y dejan de interpretar datos retirados como fallos. No se activa ni instala en un servidor al publicar la candidata. No se garantiza ahorro antes de la comparación remota.

# 4.0.0-rc26 — candidata: etiqueta real del ciclo y control de empaquetado

Corrige el rechazo de actualización observer-v4 de rc25. Su colector ejecutaba el reparto nuevo pero escribía COLLECTOR_RELEASE rc21 en el marcador; verify_new_observer_cycle exigía la versión candidata y la transacción revertía la actualización. rc26 alinea ambas versiones, sin relajar la comprobación de versión, frescura, inicio del ciclo ni componentes fallidos. Mantiene el reparto de accesos y sus límites.

El builder rechaza un marcador de versión ausente o distinto del instalador. La nueva integración extrae el colector del payload distribuido, ejecuta main con inventario/probes sintéticos, escribe spool/estado/marcador reales y llama al verificador de upgrade. Reproduce el fallo con el artefacto rc25 congelado y verifica el payload actual. No fabrica un JSON con la versión esperada. Los artefactos anteriores, la agrupación rc24 y las referencias legacy quedan intactos. No usar rc25 para observer-v4 ni forzar una instalación tras rollback incompleto. Candidata pendiente de aceptación remota.

# 4.0.0-rc25 — candidata: presupuesto de accesos por demanda

El reparto anterior podía omitir parte de un domlog grande aun cuando el tráfico conjunto cabía holgadamente en el presupuesto. rc25 calcula cuotas por bytes pendientes: satisface primero las colas pequeñas y reparte lo restante entre las grandes. Conserva 8 MiB por ciclo, 2 MiB por archivo, rotación de prioridad, una apertura por archivo, cadencias, parser y estado. El crecimiento tras el descubrimiento aprovecha capacidad libre y puede compartir la reserva de forma acotada, conservando lectura para archivos activos conocidos.

Dos nuevas métricas agregadas por host muestran bytes leídos y el máximo backlog observado por archivo. No exportan rutas ni contenido. La cobertura sigue siendo parcial ante rotaciones, inicialización, errores de formato, archivos inaccesibles o límites reales. No recupera datos omitidos ni retrocede cursores.

La regresión sintética de un archivo con más tráfico y mil pequeños falla con el colector rc21 publicado y pasa con rc25; verifica lectura completa dentro del presupuesto, offsets finales y ausencia de replay. También se verifican exceso real de presupuesto, equidad, límite por archivo, crecimiento concurrente y una apertura por archivo. Admite rc24 con hashes del payload original; mantiene las rutas previas y su reversión. La agrupación rc24, las referencias legacy y los artefactos anteriores quedan intactos. Candidata pendiente de aceptación remota y comparación del consumo en piloto.

# 4.0.0-rc24 — candidata: grupos acotados por fecha original

Corrige la agrupación opcional minimal-v4: los temporizadores de llegada de rc21–rc23 no garantizaban una distancia máxima de 300 segundos entre fechas originales. rc24 agrupa además por ventana fija UTC de cinco minutos calculada desde la fecha original. Conserva precisión de microsegundos y cantidades; cada resumen abarca menos de 300 segundos. Una llegada tardía puede emitir otro resumen para una ventana anterior: sumar occurrences, no contar filas. La clave interna se elimina antes de enviar. El tiempo de recepción sigue sujeto a los temporizadores y al transporte.

Sólo LSAPI abort y SQL Commands out of sync no críticos se agrupan. Críticos, otros errores y fechas no interpretadas pasan íntegros. No elimina lectores ni agrega sondeos, cron o contexto cPanel. No modifica filtros, fuentes, credenciales, métricas, buffers ni checkpoints. Los resúmenes siguen teniendo estado en memoria, sin garantía de auditoría y sin corregir la causa de aplicación.

La migración explícita --aggregate-errors reconoce el payload y el grafo originales rc23 además de rc21/rc22; rechaza drift. Reinicia únicamente Vector. Las pruebas nativas del lector completo verifican ráfagas tardías, orden alterado, offsets UTC, bordes exactos con microsegundos, conteos, rutas críticas y cierre del temporizador con la entrada abierta. El caso de borde falla contra el grafo rc23 congelado. Pruebas de migración y reversión preservan configuración ajena, permisos y cursores. Los artefactos previos quedan inmutables. Candidata pendiente de aceptación remota.

# 4.0.0-rc23 — candidata: fechas nativas del lector y conteos completos

Sustituye la agrupación opcional rc22. La normalización heredada entrega dt como timestamp nativo de VRL. rc22 sólo probaba fechas JSON en texto: su aserción string!(.dt) abortaba el remap y el evento original continuaba hacia reduce, sin occurrences ni first_seen/last_seen. Las métricas del host y los lectores seguían funcionando, pero no debía considerarse validada esa agrupación.

rc23 serializa tanto timestamps VRL como fechas en texto con to_string antes de parsearlos y reducir enteros de microsegundos. Conserva conteos y dt/first_seen/last_seen exactos. Si el lector marca timestamp_unparsed, el evento pasa completo, sin convertir la fecha de observación en una fecha original. Los críticos mantienen su ruta completa.

La prueba nativa recorre ahora normalize_errors → useful_errors → agrupación con el parser y filtro originales. Verifica eventos LSAPI y SQL sintéticos, timestamps VRL reales, fechas en texto, offsets UTC, orden alterado, conteos, precisión de microsegundos, bypass crítico y fechas no interpretadas; rechaza advertencias de mapping/reduce. La prueba rc22 anterior con fechas convertidas a timestamp reproduce el fallo antes de la corrección.

Admite migración explícita desde los artefactos originales rc21 y rc22 mediante `preflight --aggregate-errors`, `upgrade --aggregate-errors` y `verify`. Comprueba el runtime, las configuraciones congeladas y la integridad antes de reemplazar el grafo. Conserva fuentes, lectores, métricas, credenciales, permisos, propietarios, buffers y cursores. Reinicia únicamente Vector para aplicar el ajuste; la transacción de reversión se prueba con el payload rc22 original.

No usar la agrupación rc22 para nuevas instalaciones. Las releases anteriores permanecen inmutables. Los observer-v4 sin agrupación opcional pueden permanecer en rc21. No reconstruye conteos o fechas ausentes en resúmenes históricos. Candidata pendiente de aceptación remota tras instalarla.

# 4.0.0-rc22 — candidata: fechas originales en resúmenes de errores

Corrige la agrupación opcional de rc21 para el transporte heredado minimal-v4. Vector reduce sólo acepta números en min/max; rc21 descartaba dt, first_seen y last_seen al recibir fechas ISO como texto. Los conteos se conservaban, pero Better Stack usaba una fecha de recepción en lugar de la fecha original.

rc22 convierte las fechas a enteros de microsegundos antes de reducir y reconstruye dt/first_seen/last_seen en UTC después. dt corresponde al primer evento. Conserva el conteo, separación por sitio/tipo/gravedad y cierre de hasta300s. Soporta eventos fuera de orden y distintas zonas horarias. Fechas inválidas o ausentes pasan por la ruta completa sin agrupar; no se inventan fechas originales. Los críticos y demás eventos siguen pasando completos.

`preflight --aggregate-errors` y `upgrade --aggregate-errors` reconocen el artefacto original rc21, verifican su runtime, configuración exacta e integridad y sustituyen sólo el grafo de agrupación revisado. Conservan fuentes, lectores, credenciales, métricas, data_dir, buffers y cursores. Reinician únicamente Vector para aplicar el grafo; hay respaldo/reversión de configuración y unidades. Los grupos aún en memoria no son un registro de auditoría. No se recuperan fechas ya descartadas por resúmenes antiguos.

La actualización sin esa opción conserva el grafo heredado existente; no activar la agrupación implícitamente. Los servidores observer-v4 en rc21 no necesitan esta corrección. rc21 y todas las releases anteriores permanecen inmutables.

Pruebas nativas de Vector0.60.0 verifican fechas exactas y precisión de microsegundos, entrada fuera de orden/offsets UTC, conteos, sitios separados, cierre por temporizador con entrada abierta, bypass crítico/fechas inválidas y ausencia de advertencias de combinación. Las pruebas con el payload rc21 original verifican migración explícita, preservación de permisos/propietarios/métricas/cursores, rechazo de drift y reversión ante fallo de transporte. Candidata pendiente de aceptación remota en destino.

# 4.0.0-rc19 — candidata: perfiles conservados por actualizaciones antiguas

Corrige el rechazo observer_profile_version_mismatch de rc18 al revisar una instalación rc6/rc7 que conserva un perfil anterior. Las actualizaciones oficiales rc4..rc7 reemplazaban identidad/mantenimiento, pero no reescribían el perfil del recolector. La versión instalada y la del perfil podían diferir legítimamente.

Sólo para identidad rc6/rc7 se admite la versión original del perfil rc3..rc6 o rc3..rc7, respectivamente. Antes se exige coincidencia del runtime/colector con el artefacto oficial y de todos los archivos/configuración con la baseline existente. No se modifica ni recertifica el perfil para pasar preflight. Versiones desconocidas, ausentes o futuras siguen rechazándose. La misma regla se repite bajo lock antes de actualizar. Para versiones posteriores se conserva la igualdad estricta.

Añade ruta revisada desde rc18, con hashes del payload original y preservación del almacenamiento/pipeline existentes. No es necesario volver a actualizar de inmediato los hosts rc18 ya aceptados: la política del agente es la misma. rc18 se conserva inmutable; no usar para continuar las rutas rc6/rc7.

Incluye regresión rc3→identidad rc6/rc7 con perfil intacto, rechazo de drift/perfiles desconocidos/futuros, actualización/reversión y transición desde payload rc18 real. Sin cambios de política, cadencias, recursos, Vector, fuente o credenciales. Sólo cambia la marca de release del colector. Aceptación nativa/remota de rc19 pendiente.

# 4.0.0-rc18 — candidata: actualización revisada y procedimiento más corto

Admite observer-v4 rc10 y rc17 además de las rutas previas. Desde rc6/rc7/rc10/rc12/rc13 refresca el colector a la política de recursos cada300s y logs de incidentes, añade almacenamiento si falta y exige una ejecución nueva saludable. rc15/rc17 conservan su almacenamiento existente. Verifica payloads originales, destino/capacidades, configuración, baseline y servicios. Fuente/token/Vector/cursores/buffers se conservan. rc3/rc4/rc5 mantienen su ruta histórica de identidad; no atribuirles la política eficiente sin revisión específica.

Admite además los layouts heredados AlmaLinux wordpress-v2 rc7 y wordpress-minimal-v4 rc10: compara configuración activa con blueprint empaquetado, auxiliares y rutas/unidades exactas, y actualiza sólo identidad/mantenimiento. Conserva lectores WordPress, relay y métricas existentes; no introduce un segundo recolector ni atribuye LVE/almacenamiento SMART nuevo a estos layouts. No es conversión universal de Vector.

upgrade y preflight pueden omitir --target: usan el destino de la instalación completa, validado contra hostname/IP/cPanel/capacidades. Un perfil explícito distinto se rechaza. La repetición de upgrade, que ya existía en rc17, ahora verifica además versión completa, capacidades y bytes del runtime/colector distribuido antes de declarar sin cambios. install sobre una versión anterior indica usar upgrade; no informa actualización inexistente.

Al terminar instalación/upgrade/recuperación se invoca el publicador systemd protegido existente. No hace falta ejecutar systemctl start manualmente. Si no logra publicar, mantiene la instalación local saludable y avisa pending_hourly_retry; el timer reintenta. No confunde solicitud/HTTP con aceptación remota de métricas. Salida de instalación más breve; verify conserva diagnóstico completo. La comprobación local no publica.

El comportamiento del agente frente a rc17 no cambia: sólo su marca de release. Sin nuevas lecturas, red, cadencias, cuotas, retenciones ni borrados. Los payloads anteriores quedan inmutables. Candidata pendiente de ensayo nativo y aceptación remota por ruta.

# 4.0.0-rc15 — candidata: recuperación temprana DNF y diagnóstico acotado

No consulta repositorios para ca-certificates ya instalado (rpm -q local). Un fallo DNF conserva diagnóstico acotado con URLs eliminadas; ya no se pierde la explicación detrás del código genérico. Timeouts de paquetes600s y etapa de alta1200s, sin esperas ilimitadas ni tareas de repositorio durante monitoreo.

--recover-bootstrap explícito sólo para preflight/resume de fallo DNF antes de crear Vector, usuario, directorios, configuración o servicios propios. Valida destino/marcador/error y ausencia de bases, usuario y unidades/drop-ins desconocidos; rechaza Vector existente. Respalda marcadores antes de reanudar con la credencial guardada; no borra estado ni vuelve a pedir token. Una base que haya avanzado necesita revisión distinta. Sin cambios al recolector, almacenamiento, Vector o políticas de lectura frente a rc14.

Pruebas de validación sin mutaciones, rechazo de destino/fase/fallo distinto, usuario/base/symlink/drop-ins existentes, preservación de credencial/error, salto de DNF con certificados instalados y diagnóstico sin URLs privadas. Recuperación real y aceptación remota pendientes; no promoción estable ni despliegue masivo.

## Historial

# 4.0.0-rc14 — candidata: capacidad local, MD RAID y SMART acotado

Agrega storage.py como sonda breve separada en altas nuevas y actualización revisada observer-v4 rc13. Capacidad/inodos y opciones del montaje real por minuto, deduplicación de CageFS sin excluir el volumen original de MySQL, y estado MD RAID. Dispositivo comprobado contra mountinfo del host mediante descriptor al montaje; error explícito si está oculto o cambió. No hay lectura recursiva ni cambios en Vector, aplicaciones o unidades existentes del colector.

SMART ATA/NVMe disponible sin instalar paquetes: una consulta de hasta5s por ciclo, máximo cada6h por disco. Consulta ATA sin despertar standby, sin autodetección, self-tests o cambios de configuración. Transportes sin soporte, herramienta ausente, errores y valores desconocidos se informan sin salud inventada. Se descartan seriales, WWN y salida cruda; no se reemiten valores viejos como nuevos. Métricas de antigüedad separadas.

CPUQuota5%, MemoryMax96M y TimeoutStartSec45s de la nueva sonda; cuotas existentes intactas. ProtectSystem=strict, ProtectHome=read-only, sin red IP. La sonda privilegiada ve los montajes de solo lectura y sólo consulta metadatos; Vector conserva ProtectHome=true. Streams propios de 1MiB con dos rotaciones, eventos por cambio o diarios. Identidad comprueba timer, último resultado y heartbeat de almacenamiento; nuevos archivos y unidades incluidos en integridad y reversión. La transacción secundaria no reemplaza el backup principal de actualización.

storage-preflight --host permite validar el namespace nativo en una unidad temporal antes de crear credenciales o instalar. Las pruebas locales/CI no sustituyen esa comprobación y aceptación remota. Pendientes piloto con disco /home separado y métricas de cada volumen; candidata, sin despliegue masivo. Las migraciones anteriores preservan su alcance; sólo rc13 completa suma almacenamiento en upgrade.

## Historial

# 4.0.0-rc13 — candidata: lectura concurrente sin pérdida por clasificación inactiva

Corrige la carrera entre stat y apertura de domlogs: un archivo considerado inactivo recibe presupuesto para bytes que aparezcan antes de abrirlo. Reparte presupuesto con los activos, conserva 8 MiB/ciclo y 2 MiB/archivo y sigue informando cobertura parcial por topes. Sin aumento de CPU, memoria, frecuencia ni volumen de métricas. Pruebas de crecimiento concurrente, reparto con archivos activos y 1.000 dominios sobre presupuesto.

Agrega actualización revisada de la base completa observer-v4 rc12. Exige runtime oficial, perfil y unidad de identidad reconocidos, destino/capacidades iguales, baseline intacta y servicios saludables. Backup y reversión. Pausa timers, espera el lock de un ciclo en curso (máximo60s), comprueba nuevamente baseline, reemplaza recolector/perfil y reanuda timer/identidad. Conserva Vector activo, configuración, credencial, unidades de observador, estado, spool y buffers. No detiene el colector durante sus escrituras. Espera LVE40s y servicio55s continúan.

117 pruebas locales (el caso de otro uid/gid se ejecuta en CI), incluidas migración rc12 y reversión con conservación de datos, rechazo de drift/runtime desconocido y espera acotada del lock. Vector/configuración no cambian; las pruebas nativas se conservan. Pendiente actualización real y aceptación remota; continúa candidata.

## Historial

# 4.0.0-rc12 — candidata: espera LVE y recuperación parcial revisada

Eleva la espera LVE de15a40s y TimeoutStartSec del recolector de50a55s. Mantiene CPUQuota10%, MemoryMax256M, frecuencia60s, detalle de dominios, LVE y Governor. No interpreta timeout como consumo cero ni aumenta recursos para resolverlo. Configuración de Vector intacta.

Añade --recover-partial explícito para preflight/resume de una alta rc11 interrumpida por timeout LVE conocido. Valida hash oficial, configuración y fuente, capacidades, permisos, unidades efectivas sin drop-ins, servicios detenidos, marcadores y evidencia local. No adopta layouts desconocidos ni recolectores modificados. No reescribe credencial/configuración Vector, spool, buffers o cursores ni reinstala paquetes/usuarios. Backup antes de cambios; restaura archivos/servicios si falla validación nativa, ciclo o activación de identidad.

Pruebas de espera acotada, validación sin mutaciones, rechazo de drift/configuración alterada/permisos/destino/drop-ins/estado activo y bases completas, recuperación satisfactoria preservando transporte/cursores, y reversión tras fallo de colector o identidad. La recuperación nativa y la aceptación remota siguen pendientes; no es promoción estable ni despliegue masivo.

## Historial

# 4.0.0-rc11 — candidata: CloudLinux 8 y cobertura en servidores compartidos

Admite altas nuevas CloudLinux 8 con Python 3.9–3.12 ya instalado, detección de edición y prueba LVE real. AlmaLinux 8 conserva el alcance de migraciones revisadas. Instala curl si falta, disponible en EL8; no solicita curl-minimal. Conserva el Python del sistema, SELinux, privilegios, límites de CPU/memoria y las pruebas nativas en destino.

Corrige sesgo alfabético al agotar el presupuesto de accesos: reparte 8 MiB entre archivos con datos nuevos y rota el orden; sigue limitado a 2 MiB por archivo. No sube logs crudos ni datos históricos. Expone cobertura completa/parcial sin presentar pérdidas como ausencia de tráfico. Reduce resúmenes repetidos de dominios aislados a cinco minutos, conserva métricas por minuto y presión inmediata.

Agrega CPU del recolector con hijos finalizados, máximo RSS individual de hijos y recuentos de cuentas/dominios. Conserva métricas antiguas sin cambiar su significado. Activa contabilidad cgroup de CPU/memoria para unidades nuevas. Vector y configuración de transporte no cambian. Pruebas sintéticas de 500 cuentas/1.000 dominios bajo exceso de presupuesto en dos ciclos; también rotación, deduplicación e inactividad. Esto no prueba consumo o compatibilidad nativa del servidor real.

No cambia automáticamente recolectores de instalaciones v4 anteriores: sus upgrades preservadores siguen con el alcance documentado. Pendiente preflight, instalación nativa y aceptación remota del piloto CloudLinux 8; no es release estable.

## Historial

# 4.0.0-rc10 — candidata: unidades del paquete y drop-ins efectivos

Corrige la migración v3: no presupone que todas las unidades están en /etc/systemd/system. Consulta FragmentPath y DropInPaths a systemd, verifica archivos efectivos y los incorpora a la identidad. Conserva unidades vendor, locales y drop-ins; no copia ni reemplaza unidades del paquete.

La lista completa se comprueba en preflight y antes de crear respaldo o detener servicios durante upgrade. Excluye del nuevo manifiesto los archivos del lector retirado por --simplify-context. Se conservan correcciones rc9 de permisos umask077, reversión completa y validación como usuario vector.

95 pruebas Python, incluida transacción completa con vector.service en directorio vendor, drop-in local, ausencia de archivo efectiva detectada antes de cambiar servicios y casos de recuperación. Configuración Vector sin cambios respecto rc8/rc9; CI conserva las 30 pruebas nativas. Falta aceptación nativa/remota; continúa candidata.

## Historial

# 4.0.0-rc9 — candidata: permisos y recuperación completa

Corrige write: aplica fchmod al descriptor después de fchown y antes de reemplazar el archivo. El modo solicitado ya no depende de umask 077; un archivo 0640 permanece legible por su grupo. Reversión restaura contenido/uid/gid/modo explícitamente y aplica restorecon por política si está disponible.

La reversión intenta recuperar todos los servicios previamente activos antes de restaurar enablement. Omite unidades nuevas ausentes si antes no estaban activas/habilitadas. Continúa ante fallos independientes, comprueba servicios recuperados y reporta errores acumulados; conserva el error original si también falla rollback. Los errores systemctl identifican operación y unidad sin copiar stdout/stderr ni credenciales.

La reducción context-r6 prueba lectura y validación de la configuración final como vector antes de arrancar el transporte. Para un intento rc8 fallido, utilizar rollback de rc9 para completar restauración; luego preflight y upgrade revisados. Nunca repetir rollback/upgrade de rc8. Se preservan sus artefactos sin modificación.

Validación: 92 pruebas Python (una requiere otro uid/gid y se ejerce en CI), incluidos modo 0600/0640/0644/0700 con umask 077, transacción completa con esa máscara, unidades ausentes, errores independientes, servicio inactivo y conservación de errores originales. Se conservan las 30 pruebas nativas Vector 0.60.0 y las migraciones revisadas. Falta la aceptación nativa/remota de rc9; continúa candidata.

## Historial

# 4.0.0-rc8 — candidata: AlmaLinux 8 y contexto opcional

Bootstrap con selección explícita de Python 3.9–3.12 instalado; identidad y altas nuevas usan ese mismo intérprete. No modifica Python del sistema ni instala otro. Detecta AlmaLinux 8 para migraciones v3 revisadas. Las altas EL8 y CloudLinux 8 siguen bloqueadas hasta su revisión propia.

--simplify-context sólo para context-r6 v3: prueba y valida la configuración propuesta con el Vector instalado antes de aplicar; retira lector cPanel/servicio/timer y cron rutinario; conserva errores, métricas, destinos, ACL, data_dir, cursores y relay histórico. Respaldo/reversión conserva también propietario/grupo de la configuración. Un reinicio controlado de Vector aplica este cambio explícito. Otras diferencias no revisadas siguen bloqueando. El manifiesto informa pipeline_variant=almalinux-wordpress-minimal-v4 y la reducción aplicada. Sin opción, migración preservadora.

Admite también upgrade revisado observer-v4 rc7, junto a rc3–rc6. Los layouts v4 AlmaLinux heredados siguen requiriendo revisión específica. No requiere actualizar otros hosts para resolver esta migración.

Validación local: 84 pruebas Python; 30 casos nativos y validate --no-environment con Vector 0.60.0 oficial, archivo upstream SHA256 89a9f134dcc35a6fbba9cb8ad0fec2d4802657bba6cb1c374a30660ef9456bdc. Se agrega ese control nativo a CI. La fixture local usa namespace con sólo gid 0; CI ejecuta la restauración real con un grupo distinto. Falta preflight/ejecución de systemd 239 y sonda EL8, identidad y continuidad remotas. Continúa candidata.

## Historial

# 4.0.0-rc7 — candidata: migración tras actualizar Vector

Permite migrar una instalación v3 revisada y saludable cuyo único cambio sea el binario de Vector por una actualización del paquete. Exige mismo archivo activo/instalado (dispositivo e inode), paquete propietario vector, algoritmo SHA256 y coincidencia del hash observado con FILEDIGESTS registrado en la base RPM local. Sigue rechazando otros cambios, binarios no coincidentes, paquetes desconocidos, hashes ausentes/duplicados y servicios no saludables.

Guarda el resultado de revisión en el nuevo manifiesto v4, con el hash anterior. No modifica la referencia v3, instala/reinicia Vector ni descarga paquetes. La consulta RPM ocurre sólo durante preflight/migración; no agrega trabajo periódico al agente. El comportamiento de recolección, recomendación de reinicio y política de envíos de rc6 no cambia.

Incluye migración revisada desde instalaciones nuevas observer-v4 rc6, además de rc3/rc4/rc5. Conserva las migraciones v3 de ambas familias y su respaldo/reversión.

70 pruebas locales: actualización válida en los tres layouts v3, rechazo de cambios adicionales o servicios fallidos, comprobación RPM y migración/reversión desde los cuatro payloads v4 previos. Vector y su configuración no cambian. Falta ejecutar y aceptar la migración en destino.

Referencias de formatos y hashes RPM: https://rpm.org/docs/latest/manual/tags y https://rpm.org/docs/4.20.x/manual/queryformat.html . La base RPM local es la referencia de esta comprobación; no se afirma una verificación independiente de firma.

## Historial

### 4.0.0-rc6 — candidata: recomendación simple de reinicio

Simplifica mantenimiento a reboot_recommended (true/false/null), reboot_status (indicated/not_indicated/unknown), motivo y código de salida de la sonda. Usa needs-restarting -r una vez por hora para planificar mantenimiento manual. No clasifica urgencia ni consulta KernelCare, su estado de parches, red, logs o caché. No se amplían permisos del servicio.

Corrige el lock de metadatos DNF bajo ProtectSystem=strict mediante directorios temporales separados para logs, caché y estado, además de --cacheonly. El ensayo nativo EL9 mostró el aviso de reinicio válido con stderr vacío bajo las mismas restricciones. Los temporales se eliminan al finalizar o fallar; no se cambia dnf.conf ni se escriben rutas globales de DNF.

La identidad se publica inicialmente, ante cambios y diariamente. Los ciclos horarios sin cambios no generan envío ni escritura del estado de publicación. Un fallo HTTP no avanza el estado y se reintenta. Se conservan las métricas y heartbeats del recolector cada minuto.

57 pruebas, incluidas tres versiones históricas de upgrade/reversión, estados sí/no/desconocido, errores con stdout, limpieza de logs/locks/estado y publicación inicial/cambio/recuperación/diaria sin escrituras horarias. El transporte y la configuración Vector no cambian. Falta instalar la candidata y aceptar la nueva identidad remota.

## Historial

### 4.0.0-rc5 — candidata pública

Corrige la consulta de reinicio bajo ProtectSystem=strict. DNF intenta abrir logs incluso para needs-restarting -r; la sonda ahora redirige exclusivamente sus logs auxiliares a un directorio temporal privado con permisos 0700, nivel mínimo, rotación de 64 KiB y un respaldo por log. Se elimina al finalizar o fallar. --cacheonly evita sincronizar repositorios. No se cambia dnf.conf ni se amplían permisos del servicio o se habilita escritura en /var/log.

Agrega migración revisada desde rc4 y conserva la de rc3, con hashes de sus artefactos originales, respaldo y reversión. La actualización reemplaza sólo identidad/mantenimiento; Vector, el recolector, sus perfiles, las unidades de transporte, credenciales, checkpoints y buffers se conservan.

50 pruebas: recolección, privacidad, permisos, mantenimiento, limpieza de temporales, rechazo de fallos DNF como avisos válidos y migración/reversión desde ambos payloads originales. Vector y su configuración no cambian. La reproducción nativa DNF dentro del servicio en EL9 sigue requiriendo aceptación en destino.

Referencias técnicas: https://dnf.readthedocs.io/en/latest/conf_ref.html (logdir, logfilelevel, log_size, log_rotate) y https://dnf.readthedocs.io/en/stable/command_ref.html (--cacheonly).

## Historial

### 4.0.0-rc4 — candidata pública

Corrige la ejecución de comandos que son enlaces a un programa multipropósito, como `needs-restarting` → `dnf-utils`. Se comprueban permisos y propietario de la ruta invocada y del destino, pero se conserva el nombre original al ejecutar. Evita perder la sonda de reinicio por un cambio involuntario de `argv[0]`.

Agrega el código de salida de la sonda de reinicio a la identidad, sin copiar stdout/stderr a la telemetría. La identidad informa la versión instalada desde su manifiesto; ejecutar preflight con un instalador más nuevo no atribuye esa versión al agente anterior.

Incluye actualización acotada rc3 → rc4 para instalaciones nuevas observer-v4 revisadas, completas y sanas. Verifica hashes contra el artefacto público rc3, destino, capacidades y ausencia de drift antes de modificar archivos. Respalda y actualiza sólo identidad/mantenimiento, con reversión ante fallo. Conserva recolector, perfil, fuente, credenciales, Vector, checkpoints y buffers. Sigue admitiendo las migraciones v3 revisadas; otros layouts/versiones v4 se rechazan.

El transporte Vector y las reglas de recolección no cambian. Las pruebas cubren el comando multipropósito mediante un enlace real, permisos inseguros, actualización desde el payload rc3, preservación del transporte, rechazo de código alterado/drift y reversión de una activación fallida.

Estado: candidata; CI no reemplaza la verificación del estado de reinicio en destino ni la aceptación remota. No usar para un despliegue masivo todavía.

## Historial

rc3: primer canal público reproducible con detección de AlmaLinux/CloudLinux, Solo sin LVE y credenciales locales. Su resolución de enlaces impide consultar correctamente herramientas multipropósito; actualizar a rc4 para corregirlo. Se conserva su artefacto para verificar migraciones y procedencia.


## rc16: eventos de incidentes y recursos cada cinco minutos

La candidata rc16 elimina los resúmenes periódicos normales de recursos y LVE que duplicaban métricas. Conserva cambios de límites/capacidades, comienzo de presión, recordatorio cada15min si persiste y recuperación. El tráfico sano queda sólo en métricas; site_traffic se limita a incidentes HTTP5xx, con inicio, agregación de contadores entre avisos, recordatorios cada15min y recuperación sólo tras una lectura completa sin5xx. Los errores web/WordPress/kernel existentes siguen agrupados y disponibles. La cobertura parcial conserva métricas cada minuto y eventos al cambiar/recordatorios15min. No se recuperan bytes omitidos.

Procesos por cuenta y cloudlinux-top se consultan cada300s en lugar de60s. No se repiten sus valores en los ciclos intermedios. Nuevas métricas duplika_processes_sample_age_seconds y duplika_lve_sample_age_seconds indican edad de la última consulta exitosa; sample_interval_seconds=300 y sample_failed informan intervalo y fallo persistente hasta el siguiente intento. Un fallo no produce cero ni una falsa recuperación. Al reiniciar o retroceder el reloj se consulta nuevamente. Son instantáneas: picos cortos pueden quedar entre muestras; tasas de procesos promedian el intervalo observado.

El ciclo de acceso/errores/kernel/WordPress sigue cada60s con los mismos límites8MiB/ciclo y2MiB/archivo; el menor ritmo de LVE no reduce su presupuesto ni mueve offsets. Vector sigue midiendo CPU/RAM generales cada minuto con la misma configuración. Almacenamiento y SMART conservan frecuencias y protecciones. No se consultan paquetes ni KernelCare durante el ciclo.

Heartbeat remoto sólo ante cambios de salud/cuentas/dominios o diario: no usa contadores variables en su firma. El fichero local privado collector-health.json se renueva cada ciclo, con escritura atómica, para que identidad detecte retrasos de180s/fallos sin depender de logs rutinarios. La identidad declara resource_sampling. Sin más acceso a sitios, argumentos de procesos o credenciales.

Upgrade revisado desde observer-v4 rc15 completo con almacenamiento: exige hashes oficiales de identidad/health/capabilities/colector/sonda y unidades de identidad/almacenamiento, mismo destino/capacidades y baseline íntegra/saludable. Pausa timer, espera lock, reemplaza código/perfil, ejecuta primer ciclo y sólo acepta si saludable; conserva Vector activo,fuente,token,configuración,unidades de almacenamiento,estado/cursores/buffers. Reversión acotada ante fallo. Conserva alcance previo para layouts anteriores; no habilita normalización universal ni promoción estable.

Aceptación del piloto: mínimo12–15min para ver al menos dos consultas LVE/procesos separadas300s, ciclos de acceso/almacenamiento intermedios, antigüedad/fallos y ausencia de métricas antiguas reemitidas. El número de nombres de métricas por minuto ya no será constante; no exigir66cada minuto. Comparar bytes JSON de logs y muestras de métricas por separado en ventanas iguales; no confundir con bytes facturados. En dashboard, LVE debe mostrar última observación con su fecha, sin convertir ausencia entre muestras en cero. Nunca aumentar cuota o emitir relleno para ocultar huecos.

Simulación reproducible scripts/benchmark_efficiency.py:60min,200cuentas/400sitios y dos incidentes sintéticos,12consultas de recursos frente a60,60resúmenes de errores conservados; logs JSON7013648→89524bytes(98,72%menos). Es una simulación orientada a tráfico rutinario, no predicción de ahorro real; validar en un host antes de ampliar.


## rc17: arranque de una ejecución nueva al actualizar

rc16 queda sustituida para el piloto: su comprobación inicial podía intentar iniciar la unidad mientras el job oneshot anterior seguía activando después de liberar collector.lock. systemctl start puede unirse a ese job antiguo, que no genera el nuevo marker, y el upgrade falla/revierte con collector-health.json ausente. La carrera de ciclo/job se reprodujo con estados activando/activo/inactivo; el código no espera sólo el lock.

rc17 mantiene la política eficiente de rc16 y, tras pausar el timer y reemplazar el código bajo lock, espera hasta60s a que la unidad esté inactiva antes de arrancar la nueva ejecución. No mata ciclos. Estado fallido/desconocido o timeout aborta con error específico/reversión. El marker ahora lleva versión del código y hora de inicio; se exige que sea de la versión nueva, haya comenzado después de iniciar el reemplazo, esté fresco y contenga procesos/LVE/accesos sin componentes fallidos. Un fichero viejo, incompleto o ausente no certifica éxito.

No instalar rc16 ni forzar/rebaselinar un upgrade fallido. Si la reversión automática dejó rc15 completa/saludable, preflight y upgrade rc17 con el mismo perfil; sin rollback manual,credential,install ni cambios de fuente. La validación del destino/baseline determina si se puede continuar. Configuración Vector,almacenamiento,cadencias/política de logs y límites sin cambios respecto a rc16. La aceptación remota sigue pendiente; no extrapolar simulaciones a producción.


### 4.0.0-rc20 — bounded resource preflight for new installations

Run a transient systemd LVE and basic /proc probe during fresh preflight and again before bootstrap mutation, using the collector CPU/memory/privacy constraints. Fail early on timeout, malformed output or unavailable sandbox. Report only counts and elapsed time. Skip on systems without LVE. Preserve real first-cycle health checks, all existing sampling and log policies. Add reviewed rc19 upgrade hashes; historical released artifacts remain unchanged. Native acceptance on a new host is required; no stable promotion.

## 4.0.0-rc21

Incident-only process details (blocked or at least 80% of one CPU core), bounded per-user/reason reminders, unchanged resource metrics every five minutes. Coverage recovery logs require three consecutive complete cycles; quality metrics remain per cycle.

Explicit `--aggregate-errors` migration for the byte-reviewed inherited minimal-v4 transport: non-critical LSAPI aborts and SQL sync errors become five-minute summaries with counts and time bounds. Critical and other errors bypass aggregation. Metrics, inputs, credentials, offsets and checkpoints are preserved. Aggregation is in-memory and is not an audit log; abrupt termination may lose an unflushed window. This option must be selected during version upgrade. Normal upgrades retain inherited pipelines.

Reviewed upgrade proof from rc20 added. Candidate requires native pilot and remote acceptance; no servers are automatically updated.
