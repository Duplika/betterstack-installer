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
