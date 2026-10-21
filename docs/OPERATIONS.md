# Operación

## Aceptación

1. Preflight real del servidor y destino correcto.
2. Instalación con pruebas y validación nativas de Vector bajo permisos reales.
3. Verificación local de servicios, identidad e integridad.
4. Consultar en Better Stack una identidad reciente del hostname y fuente esperados.
5. Comprobar al menos dos ciclos nuevos de métricas y los nombres esperados según capacidades.
6. Comparar ventanas completas de cobertura, CPU del colector, RSS, duración, eventos y bytes JSON almacenados. Para métricas contar muestras mediante `sum(metrics_count)`, no filas agregadas.

No inferir versión de una fuente activa o del volumen. No representar LVE ausente, Governor deshabilitado o cobertura incompleta como cero consumo. `metrics_delivery_verified=false` es intencional: la identidad no consulta recepción.

## WordPress opcional

Agregar al perfil una lista `wordpress` explícita, máximo 10 sitios, con `user`, `domain`, `docroot`, `expires_at` ISO con zona horaria. El docroot debe coincidir con metadatos cPanel y estar bajo `/home/USUARIO/`. Sólo para instalaciones nuevas en esta candidata; cambiar la selección durante upgrade requiere otra migración revisada.

Se leen al final y después incrementalmente `error_log` y `wp-content/debug.log` ya existentes. Se envían conteos por categoría, sin texto crudo. Presupuesto global de 1 MiB por ciclo; rechaza symlinks/rutas redirigidas y deja de leer al vencer. No activa debug, instala plugins ni ejecuta WordPress. El bind de lectura del docroot permanece hasta ajustar la configuración del servicio, aunque la recolección haya vencido.

## Recomendación de reinicio

Una vez por hora se ejecuta needs-restarting -r. Se informa maintenance.reboot_recommended=true y reboot_status=indicated si recomienda reiniciar por actualizaciones; false/not_indicated si no hay indicación; null/unknown si falta la herramienta, falla o produce salida de error. Es una recomendación para planificar un reinicio manual, no una clasificación de urgencia ni una comprobación de todas las vulnerabilidades.

No se ejecuta kcarectl, consulta KernelCare, modifica su caché/configuración ni habilita escritura en sus rutas. No se calculan prioridades según live patching. Los campos KernelCare/urgencia de candidatas anteriores se retiran.

DNF se ejecuta con --cacheonly y rutas separadas de log, caché y estado dentro de un temporal 0700 bajo /tmp (PrivateTmp en el servicio). Esto permite crear el lock de metadatos sin escribir en las rutas reales de DNF. Logs auxiliares a nivel mínimo, rotación 64 KiB y un respaldo por archivo; temporales eliminados también ante fallo. No se cambia dnf.conf ni se sincronizan repositorios. Esta combinación se probó bajo el servicio protegido en EL9.

La identidad completa se envía inicialmente, ante cambios de estado/integridad/versión/servicios y cada 24 horas. No se envía ni reescribe last-send.json en ciclos horarios sin cambios. Los fallos de entrega conservan el estado anterior y se reintentan en el siguiente ciclo. Las métricas y el heartbeat del recolector continúan cada minuto. La ausencia de una identidad horaria no implica un agente detenido: comprobar el heartbeat reciente y los servicios.

## Límites

EL9 con cPanel únicamente. Los nombres históricos de unidades/directorios CloudLinux se conservan en instalaciones nuevas también sobre AlmaLinux para mantener un transporte probado; `os_id` informa el sistema real.

Domlogs: presupuesto 8 MiB por ciclo y 2 MiB por archivo. Se informa pérdida de cobertura por topes/rotaciones/formato. Los buffers y spool son acotados. Procesos breves pueden escapar al muestreo de un minuto; consumo por usuario no equivale siempre a atribución exacta por dominio.

Migraciones v3: conservar pipelines, ACL y extensiones existentes. rc7 admite además una migración específica de instalaciones nuevas observer-v4 rc3, rc4, rc5 y rc6: exige marcador completo, destino y capacidades iguales, hashes oficiales de los tres helpers, recolector y unidades de identidad, snapshot sin drift y servicios sanos. Respalda la capa de identidad y revierte si falla la activación; no modifica perfiles, Vector, recolector, checkpoints ni buffers. Otras versiones/layouts v4 requieren revisión explícita. `resume` se limita a terminar identidad sobre una base nueva ya completa y sin cambios; no repara una base incompleta.

## Actualización del paquete Vector entre v3 y v4

Si la única diferencia frente a la referencia v3 es vector_running_binary_sha256 y todos los servicios están sanos, rc7 verifica que el proceso activo corresponde al mismo dispositivo/inode del Vector instalado. Consulta el paquete propietario mediante RPM, exige nombre vector, algoritmo SHA256 y coincidencia exacta del hash observado con FILEDIGESTS para esa ruta. Cualquier cambio simultáneo de configuración, perfil, auxiliares o unidades se sigue rechazando.

Es una comprobación puntual de migración y no agrega lectura horaria del binario. No descarga ni instala paquetes. Confía en la base RPM local del sistema; no constituye verificación independiente de la firma del paquete. El manifiesto v4 conserva el resultado en migration_review, incluido el hash previo; el manifiesto y la referencia v3 no se reescriben. No se altera el propietario de directorios de datos para hacer coincidir todo el paquete RPM: se conserva la separación de privilegios del monitor.
