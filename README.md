# Better Stack installer — candidata rc33

Monitoreo esencial para servidores cPanel con una migración explícita desde **observer-v4 rc26 completo o rc27/rc28/rc29 esencial, con sonda de almacenamiento**, y ajuste de host para los dos transportes AlmaLinux WordPress revisados. rc33 es candidata: no implica aceptación remota ni promoción a estable. Los artefactos previos y las referencias legacy se conservan intactos.

rc28 mueve la lectura de I/O a su colector root ya existente, sin elevar privilegios de Vector ni cambiar protecciones del sistema. Separa la frescura del heartbeat de los componentes degradados y conserva diagnósticos de comandos clasificados, sin salida privada.

rc29 espera que termine cualquier ciclo anterior después de pausar su timer y antes de tomar el bloqueo o reemplazar archivos. El rollback usa un plazo suficiente para la parada del transporte y conserva visibles los errores reales. No cambia el grafo, las frecuencias ni las protecciones de rc28.

rc30 permite seleccionar explícitamente un muestreo de host más liviano en los layouts heredados AlmaLinux WordPress. Conserva sus lectores de errores y agregaciones, unidades, usuario, fuente y cursores. No añade sondas PHP/SQL, plugins ni un segundo transporte.

## Política esencial observer-v4

| Señal | Frecuencia y alcance |
| --- | --- |
| CPU, RAM esencial, carga y uptime | 60 s |
| Errores web globales y kernel | Lectura incremental cada 60 s; eventos seleccionados |
| Volúmenes reales, inodos y Linux MD RAID | Sonda existente cada 60 s |
| Red e I/O de discos | 300 s |
| SMART | Hasta una consulta por disco cada 6 h, distribuida entre ciclos |
| Cuentas con presión | Historial cerrado de 15 min; log sólo si un recurso registra más de 10 fallos |
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
