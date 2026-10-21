# 4.0.0-rc6 — candidata: recomendación simple de reinicio

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
