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

## KernelCare y reinicios

Se consulta estado y kernel efectivo. Un aviso genérico de `needs-restarting -r` requiere revisión de componentes incluso con KernelCare actualizado. Falta de KernelCare no implica urgencia; un fallo de sonda no equivale a salud. El agente no conoce por sí solo todas las CVE ni su exposición y reporta `urgency_verified=false`.

## Límites

EL9 con cPanel únicamente. Los nombres históricos de unidades/directorios CloudLinux se conservan en instalaciones nuevas también sobre AlmaLinux para mantener un transporte probado; `os_id` informa el sistema real.

Domlogs: presupuesto 8 MiB por ciclo y 2 MiB por archivo. Se informa pérdida de cobertura por topes/rotaciones/formato. Los buffers y spool son acotados. Procesos breves pueden escapar al muestreo de un minuto; consumo por usuario no equivale siempre a atribución exacta por dominio.

Migraciones v3: conservar pipelines, ACL y extensiones existentes. rc4 admite además una migración específica de instalaciones nuevas observer-v4 rc3: exige marcador completo, destino y capacidades iguales, hashes oficiales de los tres helpers, recolector y unidades de identidad, snapshot sin drift y servicios sanos. Respalda la capa de identidad y revierte si falla la activación; no modifica perfiles, Vector, recolector, checkpoints ni buffers. Otras versiones/layouts v4 requieren revisión explícita. `resume` se limita a terminar identidad sobre una base nueva ya completa y sin cambios; no repara una base incompleta.
