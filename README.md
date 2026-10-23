# Better Stack installer

Instalador común de Duplika para monitoreo acotado de servidores **AlmaLinux 9 / CloudLinux 8 y 9 con cPanel**, más migraciones revisadas de AlmaLinux 8 con Python compatible.

**4.0.0-rc22 es candidata.** Las pruebas locales y de CI no reemplazan el preflight Linux, las pruebas nativas de Vector en destino ni la aceptación de métricas en Better Stack. No hay despliegues masivos automáticos.

**No usar rc8:** se conserva como artefacto histórico; su escritura de permisos y reversión tienen fallos corregidos en rc9.

La migración context-r6 de rc9 también tiene una suposición de ruta local de vector.service corregida en rc10. Usar rc10 para ese caso; no crear copias de unidades del paquete.

## Qué detecta

- Sistema y edición CloudLinux; Solo continúa sin LVE, sin inventar ceros.
- LVE funcional en ediciones que lo soportan; un fallo inesperado se informa.
- Métricas de host, procesos/cuentas y tráfico agregado.
- Recomendación de reinicio por actualizaciones: sí, no o desconocido, mediante needs-restarting -r. No consulta KernelCare ni clasifica urgencia.
- WordPress opcional por sitio, con vencimiento y resúmenes de errores sin texto crudo.

El código público no contiene perfiles reales, fuentes de clientes, tokens ni inventarios. Las referencias legacy son código genérico conservado para comprobar migraciones; no son instaladores independientes.

## Descargar y verificar

Descargar `releases/4.0.0-rc22/installer.sh` desde un **commit completo e identificado**, nunca ejecutar el contenido cambiante de `main` mediante un pipe. El commit y el SHA256 de confianza se comunican junto a cada entrega. Ver [verificación](docs/VERIFICATION.md).

Ejemplo de flujo, sustituyendo los marcadores por el commit y digest verificados de la entrega:

```bash
wget --https-only -O /root/installer.sh 'https://raw.githubusercontent.com/Duplika/betterstack-installer/COMMIT_VERIFICADO/releases/4.0.0-rc22/installer.sh'
printf '%s  %s\n' 'SHA256_VERIFICADO' /root/installer.sh | sha256sum --check
```

No ejecutar si la descarga o la verificación falla. No confiar en un checksum que se acaba de descargar del mismo origen como única prueba de autenticidad. GitHub Actions genera además una atestación de procedencia para cada build confiable; verificar que realmente exista antes de afirmarlo.

## Configurar un servidor

Crear previamente una fuente Better Stack exclusiva para ese servidor. Los identificadores siguientes son ficticios:

```bash
bash /root/installer.sh configure \
  --host cloud.example.net --server-ip 203.0.113.20 \
  --source-id 1234568 \
  --ingesting-host s1234568.us-west-2a.betterstackdata.com
bash /root/installer.sh preflight --target /root/perfil.json
```

`configure` crea un perfil sin secretos, con permisos 600, y no sobrescribe otro destino. La detección de sistema, edición y capacidades es automática. `preflight` comprueba el destino y los requisitos de la base; no instala paquetes ni modifica servicios. Usa un lock y archivos temporales propios.

Después de revisar el preflight, guardar la credencial localmente mediante entrada oculta (no se envía al chat ni se coloca en argumentos):

```bash
bash /root/installer.sh credential --target /root/perfil.json --credential /root/fuente.json
bash /root/installer.sh install --target /root/perfil.json --credential /root/fuente.json
bash /root/installer.sh verify
```

Si ya existe un archivo privado válido, no hace falta ejecutar `credential`. Las credenciales sólo se usan contra el host de ingesta validado; no se incluyen en el instalador público.

## Actualizar

```bash
bash /root/installer.sh preflight
bash /root/installer.sh upgrade
bash /root/installer.sh verify
```

En una instalación v4 completa, el destino se toma del manifiesto instalado y se valida contra el servidor. `--target /ruta/perfil.json` sigue disponible si se quiere comprobar un perfil explícito. La publicación de identidad al finalizar es automática; no ejecutar otro `systemctl start`.

Las rutas revisadas y la corrección de perfiles antiguos se detallan en [notas de rc19](docs/RELEASE-NOTES.md). Observer-v4 rc6/rc7/rc10/rc12/rc13 incorpora política eficiente y almacenamiento; rc15/rc17 conserva almacenamiento y refresca código. Los dos layouts heredados AlmaLinux conservan el pipeline anterior, comparado con su blueprint y auxiliares oficiales, y actualizan identidad/mantenimiento. No confundir identidad nueva con funcionalidades no instaladas en un pipeline heredado. Versiones/layouts desconocidos o con drift se rechazan, sin reinstalar ni recertificar diferencias.

`rollback` restaura la última transacción de upgrade sin retroceder checkpoints. `resume` sólo finaliza v4 si una instalación nueva dejó la base completa y su snapshot coincide; no repara estados parciales desconocidos. Migraciones entre versiones v4 distintas necesitan revisión específica.

## Operación liviana

En el recolector común: métricas generales, accesos y almacenamiento cada minuto; recursos por cuenta/LVE cada cinco minutos; logs sólo de cambios/incidentes/errores. Comprobación de mantenimiento/identidad cada hora. La identidad se envía inicialmente, ante cambios y como inventario diario compacto; no se reenvía cada hora si no cambia. Hashes sólo de código y configuración del monitor, sin token, no de logs de clientes. No se relee el binario Vector completo cada hora.

Límites de lectura, spool, buffers, CPU y memoria. Los topes de lectura pueden reducir cobertura en sitios de mucho tráfico y se informan; no se afirma ahorro de recursos sin medir ventanas comparables.

No aplica parches KernelCare, reinicia servidores, modifica PHP/MySQL/web/firewall/SELinux ni cambia retención de fuentes. Tampoco habilita WP_DEBUG ni lee wp-config.php, SQL o logs de pagos.

## Desarrollo y publicación

```bash
sudo env PYTHONPATH=src/agent python3 scripts/check.py
python3 scripts/build.py --output out/release
```

Build determinista mediante lista explícita de archivos. CI usa Python 3.9/3.12, acciones fijadas por commit, permisos mínimos y firma de procedencia sin claves privadas persistentes. Los PR sólo ejecutan pruebas sin permisos de firma/publicación. La publicación de assets de una candidata requiere ejecutar manualmente el workflow con `publish_release`; nunca sobrescribe una release existente.

Antes de publicar la primera release, activar **Settings → General → Releases → Enable release immutability**. También configurar reglas de revisión/protección de `main` y tags de versión; estos ajustes administrativos no los aplica el código.

[Seguridad y límites](SECURITY.md) · [Procedencia y descarga](docs/VERIFICATION.md) · [Operación](docs/OPERATIONS.md)


## AlmaLinux 8 y reducción explícita de contexto

El bootstrap elige un Python 3.9–3.12 ya instalado en /usr/bin (prefiere python3 si es compatible). El servicio de identidad usa el intérprete elegido; no cambia enlaces, alternatives ni paquetes del Python del sistema. AlmaLinux 8 se admite para migraciones v3 revisadas, no para instalaciones nuevas en esta candidata. CloudLinux 8 admite altas nuevas con capacidades LVE verificadas y requiere aceptación nativa en destino.

Para una base existente reconocida almalinux-wordpress-context-r6 se puede seleccionar:

```bash
bash /root/installer.sh preflight --target /root/perfil.json --simplify-context
bash /root/installer.sh upgrade --target /root/perfil.json --simplify-context
bash /root/installer.sh verify
```

La opción retira el lector cPanel, su servicio/timer y su entrada en Vector; descarta cron rutinario del journal. Conserva métricas, errores de aplicaciones, eventos de servicios/kernel/crashes, fuente, credencial, ACL, data_dir y cursores. El relay histórico queda localmente. Se comprueba primero la referencia v3 completa, se prueba/valida la propuesta con el Vector instalado y se respalda antes de cambiar archivos. Esta opción reinicia sólo Vector para aplicar el pipeline; no reinicia el servidor. Sin la opción se conserva el contexto existente. rollback restaura archivos, propietarios/grupos y estado de las unidades de la última transacción.


## Recuperación acotada rc11

Si una alta nueva rc11 quedó detenida por timeout LVE, rc12 admite `preflight --recover-partial --target PERFIL.json` y luego `resume --recover-partial --target PERFIL.json`. No repetir credential/install ni borrar estado. Requiere archivos oficiales rc11, configuración de transporte intacta, destino/capacidades iguales, unidades detenidas y deshabilitadas (oneshot estático), evidencia local del timeout y ausencia de identidad/base completas. Rechaza otros errores, cambios de código, unidades/drop-ins y layouts desconocidos. Hace backup y reversión; conserva token, spool y cursores. Sólo tras superar las pruebas nativas y el primer ciclo habilita los servicios y termina identidad. No es reparación universal.


## Actualización revisada desde rc12

rc13 corrige la lectura de un domlog que crece entre descubrirlo y abrirlo. También asigna presupuesto a esos archivos inicialmente inactivos; conserva los topes de 8 MiB por ciclo y 2 MiB por archivo y la señal de cobertura parcial al superarlos.

`preflight` y `upgrade` reconocen la base completa observer-v4 rc12 mediante hashes oficiales, mismo destino/capacidades y baseline intacta. La actualización espera un punto seguro con el lock del recolector, cambia su código y versión del perfil, y reanuda el timer. Conserva Vector activo, su configuración, credencial, unidades, spool y cursores. Si el ciclo no libera el lock en 60 segundos, aborta y restaura el estado anterior. Se requiere aceptación remota posterior; no es una actualización masiva ni una reparación de layouts desconocidos.

## Almacenamiento rc14

Altas nuevas incorporan una sonda separada para volúmenes locales montados, inodos y Linux MD RAID cada minuto. Deduplica dispositivos y prefiere el montaje canónico sobre aliases CageFS; no recorre directorios ni lee archivos de aplicaciones. La capacidad usa un descriptor fijado al volumen y comprueba su dispositivo para evitar datos de un montaje oculto o cambiado. Admite sistemas de archivos respaldados por /dev y los tipos locales enumerados en storage.py; no son inventario de volúmenes de red, swap ni dispositivos sin montar.

La sonda breve usa ProtectSystem=strict y ProtectHome=read-only: ve los puntos de montaje reales de solo lectura. Esto NO oculta los archivos al proceso privilegiado de la sonda; su código fijo sólo abre directorios para statvfs. Vector y el observador existente conservan ProtectHome y no reciben acceso adicional. La sonda no tiene red IP, usa CPUQuota=5%, MemoryMax=96M y un timeout de 45 s. No usa PrivateTmp porque ocultaría un posible volumen /tmp separado. Sus métricas se guardan en streams propios compatibles con el glob de Vector existente; logs sólo ante cambios o diariamente y rotación de 1 MiB con dos archivos anteriores por stream.

SMART es opcional según herramienta y hardware ya disponibles. No instala paquetes: consulta ATA directo y NVMe; SCSI, RAID hardware y otros transportes quedan explícitamente sin soporte en esta candidata. Una consulta de hasta 5 s por ciclo, repartida entre discos, máximo una por disco cada 6 h. ATA usa -d ata -n standby,3 para evitar autodetección que pueda despertar discos. No ejecuta self-tests, cambia energía ni habilita SMART. Un disco dormido, desconocido o inaccesible no produce salud falsa ni ceros inventados. Las métricas SMART se emiten sólo al consultar; smart_attempt_age_seconds indica antigüedad, no actualiza resultados viejos como si fueran nuevos. No almacena seriales, WWN ni respuestas crudas. No equivale a una garantía de salud física.

Diagnóstico temporal antes de instalar (sin fuente ni token):

```bash
bash /root/installer.sh storage-preflight --host cloud.example.net
```

Ejecuta la misma protección de montajes en una unidad transitoria y sólo consulta capacidad, RAID e inventario de dispositivos; no ejecuta SMART ni deja servicios persistentes. Confirmar recepción remota después de instalar, incluyendo cada volumen real. RAID/SMART desconocido no se interpreta como saludable. Los prefijos nuevos son duplika_storage_volume_, duplika_storage_raid_ y duplika_storage_smart_. Las métricas históricas de Vector no cambian.

Actualización revisada con esta sonda: base completa observer-v4 rc13 → rc14, con backup/reversión, fuente y transporte conservados. Las migraciones v4 anteriores mantienen su alcance previo y no incorporan automáticamente almacenamiento; las variantes Alma heredadas requieren revisión específica. No actualizar toda la flota ni promover a estable antes del piloto nativo/remoto.

## Recuperación temprana DNF rc15

Si una alta falló con command_failed:dnf:exit=1 antes de crear el usuario, directorios, configuración y unidades del observador, usar preflight --recover-bootstrap y resume --recover-bootstrap --credential ARCHIVO con el mismo --target. Exige marcador base_installing del destino exacto, error DNF de instalación, ausencia de Vector, de bases completas/legacy, del usuario reservado, directorios de datos y fragmentos/drop-ins de las unidades. Rechaza cualquier base parcialmente configurada. Preflight no instala paquetes; resume conserva la credencial existente y respalda marcador/error sin borrar estado. No usar install ni credential de nuevo.

rc15 evita invocar DNF para ca-certificates ya instalado. Si falla DNF conserva hasta4000caracteres de diagnóstico, con URLs eliminadas, en el error privado del instalador. No confundir un exit1 de --assumeno por cancelación deliberada con el fallo original. Los pasos de paquetes tienen timeout600s y la etapa de alta1200s, porque una primera descarga de metadatos puede tardar varios minutos. No refresca repositorios periódicamente durante el monitoreo, actualiza el sistema ni omite GPG. Almacenamiento/configuración Vector sin cambios frente a rc14; aceptación nativa/remota sigue necesaria.


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


## rc20: prueba de recursos antes de instalar

En altas nuevas con LVE disponible, preflight consulta cloudlinux-top en una unidad transitoria de systemd: CPUQuota10%, MemoryMax256M, Nice15, IOidle, ProtectSystem=strict, ProtectHome=true, PrivateTmp y NoNewPrivileges. Verifica lectura básica de /proc y la respuesta JSON/result/users de LVE, con40s de límite para LVE y50s para la unidad. Sólo imprime duración y cantidad de cuentas devueltas, nunca usuarios o datos de procesos. Las cuentas devueltas son las observadas por la consulta, no el total de cPanel.

La instalación repite la prueba justo antes del marcador base_installing y antes de paquetes/usuario/configuración/servicios persistentes del agente, para no confiar en una comprobación antigua. Un fallo detiene el alta; no eleva cuotas ni instala una sonda periódica. La unidad transitoria se recoge automáticamente. No instala paquetes en preflight, ni elimina las verificaciones del primer ciclo real del agente. Sistemas sin LVE omiten esta prueba. No prueba el recorrido completo de procesos por cuenta, la lectura de logs ni el envío remoto.

Las actualizaciones conservan su comprobación del primer ciclo y no ejecutan esta prueba de altas. rc20 admite también rc19 con hashes revisados; no cambia frecuencias, presupuestos de lectura ni política de logs/métricas frente a rc19. No obliga a actualizar servidores ya aceptados sólo por esta mejora del alta.

## rc21: detalle de procesos por incidente y cobertura estable

- CPU, memoria y LVE mantienen sus métricas y el muestreo real cada 300 segundos. El detalle `process_sample` se envía sólo ante estado D o CPU observada de al menos 80% de un núcleo. Como máximo un detalle por usuario/motivo cada 900 segundos; no se guardan claves por PID. Las métricas de todos los procesos muestreados continúan disponibles. Una muestra de procesos no detecta procesos que nacen y terminan entre muestras.
- Una cobertura parcial se registra inmediatamente y se recuerda cada 900 segundos. Su recuperación requiere tres ciclos completos consecutivos, separados por no más de 120 segundos. Las métricas de calidad siguen mostrando el resultado de cada ciclo, sin suavizado.
- Las actualizaciones conservan credenciales, destinos, offsets, checkpoints, configuración de Vector y frecuencias de almacenamiento. Se admite rc20 además de las rutas revisadas anteriores.

### Agrupación opcional del transporte heredado minimal-v4

`preflight --aggregate-errors` y `upgrade --aggregate-errors` seleccionan una migración revisada exclusivamente para `almalinux-wordpress-minimal-v4`. Debe seleccionarse durante el cambio de versión. Sin esa opción, el transporte heredado se conserva.

Agrupa únicamente `lsapi_abort` y `sql_commands_out_of_sync` no críticos, por host, servicio, sitio, virtual host, nivel y tipo de aborto. Emite resúmenes con `occurrences`, `first_seen` y `last_seen` cada cinco minutos como máximo; elimina SQL, mensajes repetidos y PIDs del resumen. Los eventos críticos y los demás tipos atraviesan la ruta sin agregación. Las consultas de frecuencia deben sumar `occurrences` para los resúmenes; contar filas ya no representa el número de errores.

Se valida el grafo con el Vector instalado antes de modificarlo y se guarda una transacción para rollback. Se conserva propietario y modo de la configuración, fuentes, buffers y métricas. El estado temporal del reduce reside en memoria: una terminación abrupta puede perder los conteos todavía no emitidos; no es un registro de auditoría. La agrupación reduce ruido, no corrige la causa de los errores de aplicación.

Validación: pruebas de actualización desde payloads publicados, ausencia de replay de métricas, límites de detalles, reinicios del estado, recuperación de cobertura, rollback de la migración, grafo nativo Vector y conteos/expiración/bypass de críticos con entradas sintéticas. Requiere piloto y recepción remota antes de ampliar el despliegue.


## Corrección de agrupación opcional rc22

Para una base minimal-v4 con agrupación rc21, usar `preflight --aggregate-errors`, `upgrade --aggregate-errors` y `verify` del instalador rc22 fijado y verificado. Sólo la opción explícita migra el grafo. Conserva conteos y fechas originales en UTC, incluso fuera de orden; errores con fechas inválidas se envían completos. Reinicia únicamente Vector para aplicar la configuración. No reescribe historial ni recupera fechas ya descartadas. Los grupos en memoria no son auditoría. Los observer-v4 sin esa agrupación pueden permanecer en rc21.
