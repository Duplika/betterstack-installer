# Better Stack installer

Instalador común de Duplika para monitoreo acotado de servidores **AlmaLinux 9 / CloudLinux 8 y 9 con cPanel**, más migraciones revisadas de AlmaLinux 8 con Python compatible.

**4.0.0-rc15 es candidata.** Las pruebas locales y de CI no reemplazan el preflight Linux, las pruebas nativas de Vector en destino ni la aceptación de métricas en Better Stack. No hay despliegues masivos automáticos.

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

Descargar `releases/4.0.0-rc15/installer.sh` desde un **commit completo e identificado**, nunca ejecutar el contenido cambiante de `main` mediante un pipe. El commit y el SHA256 de confianza se comunican junto a cada entrega. Ver [verificación](docs/VERIFICATION.md).

Ejemplo de flujo, sustituyendo los marcadores por el commit y digest verificados de la entrega:

```bash
wget --https-only -O /root/installer.sh 'https://raw.githubusercontent.com/Duplika/betterstack-installer/COMMIT_VERIFICADO/releases/4.0.0-rc15/installer.sh'
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
bash /root/installer.sh preflight --target /root/perfil.json
bash /root/installer.sh upgrade --target /root/perfil.json
bash /root/installer.sh verify
```

Migra instalaciones v3 reconocidas y completas, o una instalación nueva observer-v4 de rc3, rc4, rc5 o rc6 con código revisado, sin drift y con servicios sanos. La actualización rc3/rc4/rc5/rc6 → rc7 sólo reemplaza la capa de identidad/mantenimiento; conserva el recolector, su perfil, Vector y sus unidades de transporte. Conserva fuente, credenciales, offsets, buffers y pipelines existentes. Las variantes AlmaLinux conservan su pipeline anterior y las extensiones existentes. Si el único cambio v3 es el binario de Vector, exige que el proceso activo use el mismo archivo instalado y que su SHA256 coincida con el registrado por el paquete vector en RPM. No modifica la referencia histórica v3 ni adopta otras diferencias. No convierte cualquier instalación Vector en una instalación administrada.

`rollback` restaura la última transacción de upgrade sin retroceder checkpoints. `resume` sólo finaliza v4 si una instalación nueva dejó la base completa y su snapshot coincide; no repara estados parciales desconocidos. Migraciones entre versiones v4 distintas necesitan revisión específica.

## Operación liviana

Métricas principales cada minuto, resúmenes normales cada cinco minutos, eventos relevantes ante cambios y comprobación de mantenimiento/identidad cada hora. La identidad se envía inicialmente, ante cambios y como inventario diario compacto; no se reenvía cada hora si no cambia. Hashes sólo de código y configuración del monitor, sin token, no de logs de clientes. No se relee el binario Vector completo cada hora.

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
