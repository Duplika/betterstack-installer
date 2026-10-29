# Operación actual: candidata rc28

La nueva política requiere selección explícita desde rc26 completo o rc27 esencial. Ver [procedimiento esencial](ESSENTIAL-MONITORING.md) y [README](../README.md). Las siguientes secciones se conservan como historial de rutas y recuperación; no describen la frecuencia de la política esencial.

# Procedimiento actual rc19

No usar rc18 para continuar actualizaciones rc6/rc7: rc19 contempla perfiles originales conservados por actualizaciones de identidad. Un fallo de preflight por versión del perfil no autoriza editarlo ni rebaselinar.

Tras descarga fijada y hash verificado: `preflight`, `upgrade`, `verify`. El destino instalado se detecta automáticamente; un perfil explícito sigue validándose si se suministra. No hace falta solicitar token otra vez, reinstalar, borrar spool/offsets ni iniciar identidad manualmente. Si ya está actualizado, verifica salud/código/capacidades y no cambia archivos.

Aceptar cada servidor mediante identidad remota y dos muestras de recursos espaciadas, con continuidad de accesos y almacenamiento según su layout. Una primera recepción puede registrarse como inicial con límites explícitos; no equivale a cadencia prolongada. Medir ahorro antes/después sólo en pilotos/cambios de comportamiento. No comparar tráfico usando muestras parciales como si fueran exactas.

No borrar/recrear fuentes para limpiar historial: cambiaría credenciales y referencias. Este instalador no cambia retención ni borra datos remotos. Los pipelines AlmaLinux heredados mantienen sus métricas existentes y lectores, sin agregar un segundo agente ni forzar capacidades CloudLinux.

# Operación

## Aceptación

1. Preflight real del servidor y destino correcto.
2. Instalación con pruebas y validación nativas de Vector bajo permisos reales.
3. Verificación local de servicios, identidad e integridad.
4. Consultar en Better Stack una identidad reciente del hostname y fuente esperados.
5. En el recolector común, comprobar dos muestras nuevas de procesos y, si corresponde, LVE separadas aproximadamente300s, sin reemisión intermedia, más continuidad de accesos/almacenamiento y edades/fallos. En pipelines heredados comprobar las métricas y lectores propios, sin exigir LVE inexistente.
6. En pilotos o cambios del agente, comparar ventanas completas iguales de cobertura, CPU del colector, RSS, duración, eventos y bytes JSON almacenados. En actualizaciones repetidas de la misma política basta la comprobación de salud/cadencia/continuidad. Para métricas contar muestras mediante `sum(metrics_count)`, no filas agregadas.

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

AlmaLinux 9 y CloudLinux 8/9 con cPanel para altas nuevas; también migraciones v3 revisadas de AlmaLinux 8 usando Python 3.9–3.12 ya instalado. EL8 requiere aceptación nativa de systemd/needs-restarting en destino. Los nombres históricos de unidades/directorios CloudLinux se conservan en instalaciones nuevas también sobre AlmaLinux para mantener un transporte probado; `os_id` informa el sistema real.

Domlogs: presupuesto 8 MiB por ciclo y 2 MiB por archivo. Se informa pérdida de cobertura por topes/rotaciones/formato. Los buffers y spool son acotados. Procesos breves pueden escapar al muestreo de un minuto; consumo por usuario no equivale siempre a atribución exacta por dominio.

Migraciones v3: conservar pipelines, ACL y extensiones existentes salvo reducción explícita --simplify-context de la base context-r6 revisada. rc8 admite además una migración específica de instalaciones nuevas observer-v4 rc3, rc4, rc5, rc6 y rc7: exige marcador completo, destino y capacidades iguales, hashes oficiales de los tres helpers, recolector y unidades de identidad, snapshot sin drift y servicios sanos. Respalda la capa de identidad y revierte si falla la activación; no modifica perfiles, Vector, recolector, checkpoints ni buffers. Otras versiones/layouts v4 requieren revisión explícita. `resume` se limita a terminar identidad sobre una base nueva ya completa y sin cambios; no repara una base incompleta.

## Actualización del paquete Vector entre v3 y v4

Si la única diferencia frente a la referencia v3 es vector_running_binary_sha256 y todos los servicios están sanos, rc7 verifica que el proceso activo corresponde al mismo dispositivo/inode del Vector instalado. Consulta el paquete propietario mediante RPM, exige nombre vector, algoritmo SHA256 y coincidencia exacta del hash observado con FILEDIGESTS para esa ruta. Cualquier cambio simultáneo de configuración, perfil, auxiliares o unidades se sigue rechazando.

Es una comprobación puntual de migración y no agrega lectura horaria del binario. No descarga ni instala paquetes. Confía en la base RPM local del sistema; no constituye verificación independiente de la firma del paquete. El manifiesto v4 conserva el resultado en migration_review, incluido el hash previo; el manifiesto y la referencia v3 no se reescriben. No se altera el propietario de directorios de datos para hacer coincidir todo el paquete RPM: se conserva la separación de privilegios del monitor.


## Limpieza context-r6

--simplify-context es opcional y sólo admite preflight/upgrade sobre la base v3 reconocida. Preflight no aplica cambios; ejecuta pruebas Vector y validación sin entorno/red usando un archivo temporal privado con la credencial existente. Upgrade repite la revisión, respalda configuración y los tres archivos del lector, detiene su timer/servicio, detiene Vector, cambia el pipeline y arranca Vector. Luego activa la nueva identidad. Cualquier fallo de la transacción restaura configuración, modo, uid/gid y estado previo de servicios. No borra data_dir, posiciones, relay ni estado de origen; no toca instrumentación SQL externa.

Retira contexto cPanel y eventos programados rutinarios de cron. Se conservan errores PHP/LiteSpeed/WooCommerce, métricas y journal de servicios/kernel/crashes. El auxiliar ACL se conserva porque mantiene lectura de logs tras rotaciones. No bajar controles de drift ni ignorar un servicio de recolección fallido para migrar. El estado fallido de la identidad v3 se investiga con preflight; no se considera resuelto sólo por elegir otro intérprete.


## Recuperación de un intento rc8 fallido

No usar rc8 para instalar, actualizar ni revertir. Su umask 077 puede reducir 0640 a 0600 y bloquear lectura del transporte; su restore puede abortar ante unidades nuevas ausentes. rc9 corrige ambos y puede completar la misma transacción privada con rollback. El operador debe verificar hostname/fuente y la procedencia del respaldo. La reversión puede reiniciar Vector para recuperar el estado previo. Después comprobar preflight antes de volver a migrar. No borrar last-transaction.json ni respaldos para sortear controles.

rc9 intenta todos los reinicios previamente activos aunque falle otro y sólo después restaura enablement. Una unidad ausente antes y después no se habilita/deshabilita. Verifica servicios y reporta errores acumulados con el fallo original. Restorecon aplica política SELinux a los archivos recuperados; no desactiva SELinux ni abre permisos de otras rutas.


## Ubicación efectiva de unidades

rc10 obtiene FragmentPath/DropInPaths mediante systemctl show para cada servicio/timer conservado. Verifica todos los archivos durante preflight y antes de mutar el legado. La identidad registra las rutas reales, incluidas unidades de proveedor y drop-ins locales. No crear /etc/systemd/system/vector.service como atajo: debe conservarse la unidad del paquete.


## Escala en instalaciones nuevas rc11

El presupuesto de accesos sigue en 8 MiB por ciclo y 2 MiB por archivo. Se descubre una vez la lista de archivos canónicos, se deduplican inodes y se reparte el presupuesto entre los que tienen datos nuevos; el orden inicial rota en cada ciclo. No garantiza leer todas las solicitudes si se supera el presupuesto. duplika_access_coverage_complete=0 y duplika_access_skipped_bytes informan la cobertura parcial. Usar las tasas por sitio como observación parcial en esos intervalos, no como totales exactos de facturación. El primer ciclo establece EOF sin subir historia.

Los resúmenes de dominios aislados siguen la política de cuentas/LVE: primera observación, cambios de metadatos/presión, cada cinco minutos, y cada ciclo bajo presión. Se conservan las métricas por minuto. No se activan lectores WordPress por defecto.

duplika_collector_cpu_seconds y duplika_collector_peak_rss_bytes conservan su significado: sólo el proceso Python. duplika_collector_cpu_with_children_seconds incluye CPU de hijos finalizados; duplika_collector_children_peak_rss_bytes es el máximo individual de hijos, no suma simultánea. Ambos excluyen Vector y la identidad. CPUAccounting/MemoryAccounting en las unidades nuevas permite revisar el conjunto por cgroup con systemctl; no se afirma que estas métricas representen todo el agente. duplika_cpanel_accounts y duplika_cpanel_domains publican los recuentos del mapa de dominios.

Estos cambios del recolector/unidades se aplican a altas nuevas y a las migraciones v3 que ya reemplazaban el recolector. Los upgrades v4 revisados conservan su recolector y transporte anteriores; no extienden estas mejoras automáticamente a instalaciones existentes. No hay actualización masiva.


## Timeout LVE y base parcial rc11

rc12 permite hasta40s para cloudlinux-top, conserva CPUQuota10%/MemoryMax256M y TimeoutStartSec55s para el recolector nuevo. El timeout anterior15s puede resultar insuficiente bajo la cuota; no inferir que --domains es la causa sin comparar. No altera límites LVE, Governor, aplicaciones ni el intervalo60s. Si la sonda sigue fallando no se declara salud completa.

Recuperación explícita de la base rc11 incompleta: `preflight --recover-partial --target PERFIL.json` sólo valida; `resume --recover-partial --target PERFIL.json` repite validación, respalda archivos/estados, valida Vector como lector, cambia únicamente recolector/perfil/unidad observadora y ejecuta un ciclo nativo antes de habilitar transporte/timer e identidad. No reinstala Vector, crea usuarios, pide token ni elimina checkpoints. Conserva instalación y source_id. Ante fallo restaura archivos/estados del respaldo, conservando datos de diagnóstico y cursores que haya actualizado el ciclo real.

Exige fase base_installing y destino exactos, fallo original conocido, hash oficial del recolector rc11, perfil/capacidades/destino válidos, configuración Vector regenerada idéntica, permisos/propiedad, unidades oficiales sin drop-ins y detenidas, timer/transporte disabled y observador static. Usa el Python elegido sólo si la unidad original coincide. Rechaza marcadores completos o errores distintos. Verificar recepción remota y dos ciclos posteriormente; no ejecutar credential de nuevo ni borrar archivos para sortear un rechazo.


## Lectura concurrente y actualización rc12 → rc13

Durante discovery, un archivo sin bytes nuevos puede crecer antes de abrirlo. rc13 le reserva una porción del presupuesto, sin tomar toda la asignación de archivos ya activos. Evita la omisión por allowance=0 con presupuesto disponible. Conserva el máximo global 8 MiB y máximo por archivo 2 MiB; no promete cobertura total cuando se alcanzan.

La migración observer-v4 rc12 comprueba hashes de runtime oficiales, unidad de identidad con el Python seleccionado, perfil rc12, destino, capacidades, servicios y baseline completa antes de backup. Detiene los timers de observador/identidad y la identidad oneshot, espera hasta 60 segundos el lock existente del colector y vuelve a comprobar baseline antes de escribir. No mata el colector en curso: termina su escritura antes de reemplazar código/perfil. Luego reanuda el timer del observador y activa la identidad. No reinicia Vector, cambia configuración de transporte ni respalda/restaura cursores o spool; esos datos quedan conservados. Las unidades del observador mantienen cuotas/intervalo/tiempos de rc12.

Ante fallo restaura archivos y estados de timers mediante la transacción; un ciclo que haya actualizado legítimamente su estado conserva ese avance. La identidad local puede usar el heartbeat previo reciente: no representa comprobación del nuevo ciclo, que debe recibirse y verificarse por separado en Better Stack. Otras migraciones v4 rc3–rc7 mantienen su alcance preservador; rc8–rc11, variantes heredadas y otros layouts siguen requiriendo revisión específica.

Para capacidad de discos, contrastar el namespace protegido con df/findmnt nativos. Si /home comparte filesystem con /, la raíz cubre ese volumen. Si tiene un volumen separado, requiere una solución de lectura de capacidad revisada antes de afirmar cobertura de almacenamiento; no retirar ProtectHome para que Vector acceda a los archivos de clientes.


## Almacenamiento rc14

Ver README, sección Almacenamiento rc14. Ejecutar storage-preflight --host HOST en el destino: comprobar cada volumen local frente a df/findmnt, opciones read-only del host, RAID y ausencia de duplicados CageFS. Al instalar/actualizar rc13, la sonda debe completar su primer ciclo antes de habilitar el timer e identidad. La recepción de volúmenes y RAID se acepta aparte en Better Stack; SMART se reparte una unidad por ciclo y puede seguir desconocido. Datos físicos no disponibles en VPS no equivalen a discos sanos.

El transporte sigue activo en actualización; no cambia su token, configuración ni cursores. La sonda escribe métricas/eventos propios, independientes del spool del colector. Ante falla se restaura runtime y estados de unidades sin retroceder cursores. Si existe una unidad, drop-in o storage.py desconocido, se rechaza en vez de sobrescribir. Las instalaciones antiguas sin esta sonda no se dan por monitoreadas en todos sus volúmenes.


## Fallo de paquetes antes de crear la base

rc15 incorpora --recover-bootstrap, separado de --recover-partial rc11. Revisar el diagnóstico real: --assumeno puede devolver1 sólo por cancelar. No borrar install-pending.json, fuente, credencial o carpetas. Si Vector o alguna pieza de la base ya existe, la reanudación temprana se rechaza; investigar ese estado en vez de forzar. Usar primero preflight --recover-bootstrap --target PERFIL; luego resume con esos parámetros y --credential existente. Verificar el primer ciclo, almacenamiento y recepción de identidad/métricas después.


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
