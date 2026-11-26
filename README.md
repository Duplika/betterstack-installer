# Better Stack installer — candidata rc50

La política esencial usa el cierre diario completo de CloudLinux para WHMCS.
Retira la consulta recurrente de 15 minutos y sus métricas auxiliares.
El diagnóstico dirigido sigue disponible. El preflight puede comprobar la
lectura histórica una vez. Verificar cobertura del cierre y cantidad de filas
en el addon tras actualizar. Los paneles del histórico intradiario dejarán
de recibir datos para esta versión. Un cron diario externo reconocido conserva
la responsabilidad de su cierre.

En AlmaLinux y en instalaciones donde LVE no es compatible, el colector omite
las métricas de salud del historial LVE. La actualización desde rc45 valida los
archivos instalados y conserva el transporte Vector.

`preflight` e `install` seleccionan la base esencial común de forma
predeterminada en altas nuevas de cPanel. La base detecta CloudLinux y sus
capacidades LVE. En AlmaLinux publica las métricas de host y salud, sin
lectura periódica de errores de aplicación. Las instalaciones existentes
siguen usando sus rutas de actualización revisadas.

## Mejoras de rc44

`rc44` atribuye los OOM de CloudLinux a una cuenta o a un reseller
cuando existe una correspondencia en el inventario local de cPanel.
Conserva el ID LVE de los eventos agrupados y añade la versión del
colector a sus eventos. Admite actualizar instalaciones revisadas de
rc42 y rc43. Consultá [las notas de versión](docs/RELEASE-NOTES.md).

## Mejoras de rc42

`rc42` incorpora el muestreo de capacidad de `rc41` y conserva la detección
por minuto de fallos de almacenamiento, RAID y montajes en solo lectura.
Agrupa los abortos LSAPI atribuibles a cuentas en resúmenes de 15 minutos.

En instalaciones compatibles con el cron diario de CloudLinux, el upgrade
suprime la salida estándar del job revisado. Los errores de stderr, los
cierres enviados a Better Stack y la frecuencia del job se conservan.
El preflight comprueba la huella del publicador y acepta las variantes
revisadas del cron. Consultá [las notas de versión](docs/RELEASE-NOTES.md).


## Resumen diario de CloudLinux LVE

El observador esencial consulta el día anterior de Buenos Aires a partir de
las 00:20. Publica sólo cuentas con más de 20 faults sumados y emite un cierre
incluso si publica cero. El cierre incluye cuentas evaluadas, publicadas,
umbral y descartes. El informe administrativo aplica su propio criterio
por recurso.

La consulta tiene un máximo de 45 segundos. Se difiere cuando el ciclo está
ocupado, y los fallos reintentan
con intervalo mínimo de 15 minutos. Entre ciclos se conserva el último estado
del cierre para evitar logs de estado repetidos. Las métricas de salud siguen
en cada ciclo.

`preflight`, `upgrade` y `verify` aceptan rc38 original y la variante filtrada
revisada si su manifiesto coincide. Se conservan el estado del cierre diario,
la política existente y el transporte. Confirmá la recepción del cierre y
sus filas en Better Stack antes de extender el piloto.

## Inventario mensual de cgroups en CloudLinux

La identidad horaria toma una lectura al instalar o actualizar y una por mes
calendario UTC. Usa `stat -fc %T /sys/fs/cgroup/` y comprueba si el módulo LVE
está cargado. `cgroup2fs` indica v2 unificado. `tmpfs` en la raíz se registra
como no unificado porque también puede corresponder a un montaje híbrido.
La métrica `duplika_cloudlinux_cgroups_info` conserva el formato del piloto y
se envía por el publicador de identidad existente. Si el envío falla,
reintenta la misma muestra en el siguiente ciclo. `verify` muestra el estado
local de la muestra y la recepción se comprueba en Better Stack.

Para actualizar una instalación revisada y conservar su política de monitoreo,
usar los comandos normales después de verificar el instalador fijado al commit:

```bash
bash /root/installer-rc37.sh preflight
bash /root/installer-rc37.sh upgrade
bash /root/installer-rc37.sh verify
```

La actualización admite rc34/rc35/rc36 en los layouts revisados. Si encuentra
el temporizador piloto oficial de cgroups, lo incluye en el respaldo de la
transacción y lo retira. El rollback restaura el piloto si estaba presente.
El inventario aplica sólo a CloudLinux. No modifica la jerarquía de cgroups,
TuneD, el kernel, la configuración de Vector ni la frecuencia de los demás
colectores. `--base-monitoring` sigue disponible para seleccionar la base común.

## Base esencial común rc36

En altas nuevas, `preflight` e `install` eligen esta base. El indicador
`--base-monitoring` sigue aceptado para compatibilidad y para las rutas
de conversión revisadas. No combinar opciones de migración.

- Migración revisada: observer-v4 rc34 esencial con storage; o los dos
  transportes mínimos heredados rc35 (`--minimal-host` ya aplicado).
- Identidad común: `template_id=essential-monitoring`,
  `monitoring_policy=essential-base-v1`, con `monitoring_capabilities` explícitas.
  LVE, RAID, SMART y diagnóstico por cuenta no se inventan en perfiles de host.
  La plataforma, el pipeline y la cobertura real siguen visibles.
- CPU: suma por modo y timestamp exacto de scrape en Vector, antes del envío.
  Emite `duplika_cpu_seconds_total` con `logical_cpus` en las etiquetas; ya no
  envía `cpu_seconds_total` por núcleo. Mantiene los modos originales, incluido
  `io_wait`. Reinicios y cambios en cantidad de núcleos requieren recalentar la
  tasa; el contador agregado no se mezcla con las series antiguas.
- Para graficar CPU usada: `1 - tasa_idle / logical_cpus`. Los consumidores
  existentes deben admitir ambas representaciones antes de migrar un servidor.
- CPU/RAM/filesystem siguen cada 60 s; red/I/O cada 300 s; LVE histórico cada
  900 s cuando está disponible. No cambia fuente, credencial, offsets o buffers.
- Las sondas y el primer ciclo local deben pasar; la aceptación remota sigue
  siendo independiente. No implica promoción a estable.

Después de descargar desde el commit comunicado y verificar su SHA256:

```bash
# Servidor existente compatible: primero revisar el preflight.
bash /root/installer-rc36.sh preflight --base-monitoring
bash /root/installer-rc36.sh upgrade --base-monitoring
bash /root/installer-rc36.sh verify

# Alta nueva, tras configure/credential con el mismo instalador:
bash /root/installer-rc50.sh preflight --target /root/perfil.json
bash /root/installer-rc50.sh install --target /root/perfil.json --credential /root/fuente.json
```

El upgrade respalda y revierte ante fallos, y sólo reinicia el transporte del
monitor. El rollback explícito conserva datos de ejecución y cursores:
`bash /root/installer-rc36.sh rollback`. El despliegue exige un piloto por familia
antes de continuar con los demás servidores. La base común también admite
altas nuevas en AlmaLinux 8 si pasan las comprobaciones del instalador.

## Historial y rutas anteriores

Monitoreo esencial para servidores cPanel con una migración explícita desde **observer-v4 rc26 completo o rc27/rc28/rc29 esencial, con sonda de almacenamiento**, y ajuste de host para los dos transportes AlmaLinux WordPress revisados. rc34 es candidata: no implica aceptación remota ni promoción a estable. Los artefactos previos y las referencias legacy se conservan intactos.

rc28 mueve la lectura de I/O a su colector root ya existente, sin elevar privilegios de Vector ni cambiar protecciones del sistema. Separa la frescura del heartbeat de los componentes degradados y conserva diagnósticos de comandos clasificados, sin salida privada.

rc29 espera que termine cualquier ciclo anterior después de pausar su timer y antes de tomar el bloqueo o reemplazar archivos. El rollback usa un plazo suficiente para la parada del transporte y conserva visibles los errores reales. No cambia el grafo, las frecuencias ni las protecciones de rc28.

rc30 permite seleccionar explícitamente un muestreo de host más liviano en los layouts heredados AlmaLinux WordPress. Conserva sus lectores de errores y agregaciones, unidades, usuario, fuente y cursores. No añade sondas PHP/SQL, plugins ni un segundo transporte.

## Política esencial observer-v4

| Señal | Frecuencia y alcance |
| --- | --- |
| CPU, RAM esencial, carga y uptime | 60 s |
| Errores web globales y kernel | Lectura incremental cada 60 s; eventos seleccionados |
| Volúmenes reales, inodos y Linux MD RAID | Capacidad e inodos cada 5 min; estado de montaje y RAID cada 60 s |
| Red e I/O de discos | 300 s |
| SMART | Hasta una consulta por disco cada 6 h, distribuida entre ciclos |
| Cuentas con presión | Cierre del día anterior por cuenta y recurso; marca de completitud incluso con cero cuentas publicadas |
| Inventario cPanel | Recuentos cada 12 h; mapa cacheado e invalidado por metadatos de userdomains/passwd |
| Salud | Heartbeat, fallos, duración, RSS y CPU con hijos cada 60 s |
| Accesos, procesos y detalle LVE/WordPress por cuenta | Desactivados por defecto; sólo diagnóstico explícito de hasta 60 min |

No se emiten series normales por cuenta ni tráfico rutinario por sitio. No hay filtro adicional por porcentaje de consumo. Los contadores de fallos son de CloudLinux, no cantidades de solicitudes o incidentes independientes. MySQL se adjunta como contexto cuando la consulta histórica lo devuelve para una cuenta seleccionada; no se afirma cobertura independiente de restricciones Governor sin fallos LVE.

## Descargar y verificar

Usar el commit completo y el digest comunicados con esta entrega. Nunca ejecutar un pipe de una rama cambiante:

```bash
wget --https-only -O /root/installer-rc30.sh 'https://raw.githubusercontent.com/Duplika/betterstack-installer/COMMIT_VERIFICADO/releases/4.0.0-rc30/installer.sh'
printf '%s  %s\n' 'SHA256_VERIFICADO' /root/installer-rc30.sh | sha256sum --check
```

La candidata está empaquetada en el repositorio; eso no certifica una GitHub Release publicada o una atestación emitida. Ver [procedencia](docs/VERIFICATION.md).

## Selección explícita del piloto

```bash
bash /root/installer-rc30.sh preflight --essential-monitoring
bash /root/installer-rc30.sh upgrade --essential-monitoring
bash /root/installer-rc30.sh verify
```

En observer-v4, revisar primero la salida del preflight: exige muestras reales de contadores I/O bajo restricciones estándar del colector y valida historial/schema de CloudLinux, costo de esa consulta, salud/cobertura de almacenamiento y grafo nativo Vector sin aplicar la migración. El upgrade repite las comprobaciones, respalda y revierte ante fallo. Reinicia sólo el transporte del monitor al cambiar el grafo; no reinicia el servidor ni aplicaciones. Conserva fuente, token, buffers, data_dir y offsets.

La opción acepta rc26 observer-v4 o rc27/rc28/rc29 esencial con sonda y grafo revisados; también los dos layouts AlmaLinux descritos abajo. No adopta rc25 incompleto ni convierte arbitrariamente otros pipelines. Las rutas anteriores sin esta opción mantienen su alcance previo y **no activan** la política esencial. No usar una actualización genérica como sustituto de esta selección. No hay despliegues masivos automáticos.

## WordPress liviano en los transportes AlmaLinux heredados

Sólo para `almalinux-wordpress-v2` y `almalinux-wordpress-minimal-v4` con configuración y runtime oficiales revisados. La selección `--essential-monitoring` identifica la política `essential-host-v1`, distinta de `essential-v1` del observador.

| Señal | Alcance |
| --- | --- |
| CPU, RAM esencial, carga, uptime y filesystem | 60 s; mantiene exclusiones de filesystem existentes |
| Red e I/O de discos | 300 s mediante Vector, con sus nombres actuales |
| Errores WordPress, PHP y WooCommerce | Lectores incrementales, filtros y agregaciones existentes sin cambios |
| Disponibilidad externa, cron y colas WordPress | No incluidos; requieren diseño y aceptación separados |
| Sonda root de almacenamiento/SMART y LVE | No añadidos a estos layouts |

El preflight ejecuta una sonda Vector temporal de sólo host, sin destino remoto, como el usuario del transporte. Exige muestras válidas de filesystem, red y cuatro contadores por disco. Esa prueba no reproduce todo el sandbox systemd: la aceptación exige comprobar la unidad real y recepción remota tras el cambio. Rechaza usuarios no revisados o un segundo transporte activo.

El upgrade detiene brevemente y vuelve a iniciar la misma unidad existente, conservando lectores, IDs, perfil, credencial, `data_dir`, buffers y offsets. El `vector.service` heredado puede ser el emisor correcto de este layout. No se inicia otra unidad Vector. La comparación de costo usa CPU por cgroup, memoria, eventos por categoría y muestras por familia durante 24 h equivalentes; el tamaño JSON no equivale a bytes facturados.

## Diagnóstico de una cuenta observer-v4

```bash
bash /root/installer-rc30.sh diagnostic-enable --user example --minutes 30 --readers access lve
bash /root/installer-rc30.sh diagnostic-status
bash /root/installer-rc30.sh diagnostic-disable
```

Cuenta exacta, máximo 60 min, activación en el siguiente ciclo. Lectores: access, lve, processes y wordpress. El lector de accesos sólo abre dominios reconocidos de esa cuenta; procesos y LVE sólo exportan sus datos. El estado de diagnóstico es separado de los cursores de la operación anterior y se retira al vencer. No se habilita automáticamente a partir de una alerta.

WordPress sólo admite rutas y vencimientos previamente aprobados en el perfil y sandbox instalado. Si no existen, se rechaza: esta candidata no añade binds ni acceso a nuevos docroots. No se lee wp-config.php, no se ejecuta WordPress ni se habilita debug.

## Aceptación y reversión

Comprobar recepción remota de host/colector/sonda, ausencia de series rutinarias retiradas una vez drenado el transporte, al menos dos ventanas históricas completas y vencimiento del diagnóstico. Comparar 24 h de CPU total por cgroup, memoria, eventos y muestras antes/después. No equiparar muestras o JSON local con bytes facturados.

```bash
bash /root/installer-rc30.sh rollback
```

Restaura la última transacción, sin retroceder offsets. El estado de ventanas tiene IDs deterministas; una interrupción entre escribir spool y confirmar estado puede duplicar un log. Deduplicar por host, window_id y recurso al sumar; no afirmar exactly-once ni recuperación de ventanas perdidas.

[Operación rc30](docs/ESSENTIAL-MONITORING.md) · [Dashboards](docs/DASHBOARDS.md) · [Historial](docs/RELEASE-NOTES.md) · [Seguridad](SECURITY.md)

## Desarrollo

```bash
sudo env PYTHONPATH=src/agent python3 scripts/check.py
python3 scripts/build.py --output out/release
python3 scripts/check_vector.py --vector /ruta/vector
```

Pruebas sintéticas, build reproducible y Vector nativo no sustituyen aceptación del servidor. Configuración, inventarios, logs y credenciales reales permanecen fuera del repositorio público.

## rc31: mantenimiento diario de LiteSpeed (candidata)

Sólo en instalaciones LiteSpeed Enterprise detectadas por su binario y metadatos,
consulta el catálogo oficial STABLE (versión y build) y la versión estable PECL de
timezonedb para los PHP EasyApache presentes. No ejecuta lsup/update/buildtimezone,
compila módulos, cambia INI/CageFS ni reinicia servicios. OpenLiteSpeed y otras
ediciones no se interpretan como Enterprise. Una instalación ausente no dispara
consultas de red ni procesos PHP. La detección soporta LSWS_HOME.config validado.

La identidad horaria reutiliza un resultado privado por 86400 segundos, también
si la consulta falla. Sólo se consulta al publicar, nunca en verify/preflight.
Los dos catálogos usan HTTPS sin redirecciones/proxy/curlrc, 6 s y 16 KiB por
respuesta. Máximo 12 runtimes EA4, cada sonda PHP con 3 s; usa -n y carga sólo el
módulo root validado, sin configuración PHP ni código de sitios. La habilitación
se comprueba en los INI globales EA4 conocidos; no acredita el estado de workers
y sitios en ejecución ni rutas de configuración personalizadas. Permisos,
versiones o respuestas desconocidas producen estado desconocido, nunca saludable.

El resultado distingue versión/build nuevo, instalado por delante del canal
estable (sin recomendar downgrade), extensión ausente/deshabilitada y fallos.
No se usa autoupdate/build como catálogo fresco. BUILD.VERSION tiene prioridad
sobre BUILD. No se compara con candidatos ni se promete detectar builds que
LiteSpeed no haya promovido a su canal STABLE.

Las métricas diarias conservan la fecha original de observación y reintentan
entrega sin repetir la consulta. Nombres: duplika_litespeed_update_state,
duplika_timezonedb_update_state y duplika_timezonedb_php_update_state. Valores:
-2 no aplicable, -1 desconocido, 0 sin actualización pendiente, 1 actualización,
2 extensión faltante. Etiquetas incluyen status y versiones; PHP sólo en la
métrica por runtime. Una consulta con más de 36 h debe mostrarse vencida, nunca
como actual. No se agregan alertas de paging por mantenimiento rutinario.

Prueba puntual, sin fuente/credencial ni instalación del agente:

```bash
bash /root/installer.sh maintenance-check
```

Tras revisar la salida y los costos nativos, una base v4 rc30 íntegra admite
preflight/upgrade/verify normales; conserva la política esencial ya elegida,
transporte, credenciales, buffers y offsets. Publicar la candidata no la instala.
Se requiere piloto y aceptación remota antes de extender a la flota.

### rc32: metadatos propiedad de lsadm

El marcador autoupdate/release puede pertenecer legítimamente a lsadm. rc32 lo
lee como dato acotado (256 bytes), con descriptores fijados y O_NOFOLLOW, sólo
propietarios root/lsadm y sin escritura de grupo/otros. Rechaza enlaces, FIFO,
archivos especiales y otros propietarios; no modifica permisos. No se relaja la
confianza root del ejecutable, VERSION, BUILD.VERSION, PHP, módulos, INI ni del
agente. Diagnóstico local incluye etapa y categoría fija, sin salida privada.
Invalida la caché anterior una vez al cambiar el esquema de la sonda. Candidata
para repetir el piloto; no se presume aceptada en servidor ni desplegada.

### rc33: reinicio recomendado y diagnóstico timezoneDB

El detector local needs-restarting -r existente continúa una vez por hora con
--cacheonly, sin refrescar repositorios, con directorios temporales privados y
límite de 20 segundos. Ahora publica duplika_reboot_recommended junto a la
identidad sólo al cambiar o en el inventario diario: 1 recomendado, 0 no indicado,
-1 desconocido. Aplica a todos los servidores, tengan LiteSpeed o no. Es una
recomendación por actualizaciones, no un indicador de urgencia o evaluación de
KernelCare; nunca reinicia ni instala paquetes si falta la herramienta. El
chequeo puntual maintenance-check incluye el resultado de reinicio.

La lectura de INI EA4 acepta enlaces sólo root, con destino dentro del mismo
runtime; comentarios UTF-8 no se consideran fallo. Conserva PHP -n y no ejecuta
los INI ni aplicaciones. Errores por versión PHP identifican la etapa y categoría
fija. Esquema de caché nuevo para repetir resultados previos al actualizar.
Candidata: requiere piloto nativo, verificación del sandbox y recepción remota.

### rc34: INI root:root con escritura de grupo

Admite INI regulares root:root con modo 0664 como datos; mantiene rechazo de
escritura para otros, escritura de otros grupos, propietarios no root, rutas
fuera del runtime y directorios inseguros. Lee como máximo 256 KiB mediante
descriptor O_NOFOLLOW/O_NONBLOCK. Nunca carga ese INI en PHP (-n). La confianza
de binarios, módulos, credenciales e integridad no cambia; no corrige permisos.
Renueva el esquema de caché para repetir el resultado al actualizar. Candidata
pendiente de repetir el piloto; los artefactos rc33 permanecen inmutables.


## Base mínima de host rc35

En transportes heredados revisados con esencial ya seleccionado (rc30/rc34),
`preflight --minimal-host` valida la retirada de todos los lectores de logs de
aplicaciones/journal/check y del timer ACL. `upgrade --minimal-host` aplica la
transición con respaldo/reversión. No combinar con otras opciones de migración.
Conserva fuente/token, Vector, data_dir, métricas esenciales y publicación directa
de identidad/mantenimiento. No instala observer-v4 ni añade almacenamiento
privilegiado. Verificar recepción remota de CPU/RAM/filesystem/disco/red después.
