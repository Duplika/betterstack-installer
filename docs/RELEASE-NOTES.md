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
