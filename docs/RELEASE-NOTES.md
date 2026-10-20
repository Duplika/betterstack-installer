# 4.0.0-rc4 — candidata pública

Corrige la ejecución de comandos que son enlaces a un programa multipropósito, como `needs-restarting` → `dnf-utils`. Se comprueban permisos y propietario de la ruta invocada y del destino, pero se conserva el nombre original al ejecutar. Evita perder la sonda de reinicio por un cambio involuntario de `argv[0]`.

Agrega el código de salida de la sonda de reinicio a la identidad, sin copiar stdout/stderr a la telemetría. La identidad informa la versión instalada desde su manifiesto; ejecutar preflight con un instalador más nuevo no atribuye esa versión al agente anterior.

Incluye actualización acotada rc3 → rc4 para instalaciones nuevas observer-v4 revisadas, completas y sanas. Verifica hashes contra el artefacto público rc3, destino, capacidades y ausencia de drift antes de modificar archivos. Respalda y actualiza sólo identidad/mantenimiento, con reversión ante fallo. Conserva recolector, perfil, fuente, credenciales, Vector, checkpoints y buffers. Sigue admitiendo las migraciones v3 revisadas; otros layouts/versiones v4 se rechazan.

El transporte Vector y las reglas de recolección no cambian. Las pruebas cubren el comando multipropósito mediante un enlace real, permisos inseguros, actualización desde el payload rc3, preservación del transporte, rechazo de código alterado/drift y reversión de una activación fallida.

Estado: candidata; CI no reemplaza la verificación del estado de reinicio en destino ni la aceptación remota. No usar para un despliegue masivo todavía.

## Historial

rc3: primer canal público reproducible con detección de AlmaLinux/CloudLinux, Solo sin LVE y credenciales locales. Su resolución de enlaces impide consultar correctamente herramientas multipropósito; actualizar a rc4 para corregirlo. Se conserva su artefacto para verificar migraciones y procedencia.
