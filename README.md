# Better Stack installer

Instalador común de Duplika para monitoreo acotado de servidores **AlmaLinux 9 / CloudLinux 8 y 9 con cPanel**, más migraciones revisadas de AlmaLinux 8 con Python compatible.

**La candidata de referencia es 4.0.0-rc26, disponible en el [PR #7](https://github.com/Duplika/betterstack-installer/pull/7).** El código de `main` sigue en rc21 hasta integrar esa candidata; esta actualización de documentación no cambia el instalador ni promueve una versión a estable. El artefacto rc26 está fijado al commit `777e541b43b2a625d966d6f66b4adbe42636b32b`. Las pruebas locales y de CI no reemplazan el preflight Linux, las pruebas nativas de Vector en destino ni la aceptación de métricas en Better Stack. No hay despliegues masivos automáticos.

**No usar rc8:** se conserva como artefacto histórico; su escritura de permisos y reversión tienen fallos corregidos en rc9.

La migración context-r6 de rc9 tenía una suposición de ruta local de vector.service corregida desde rc10. Las candidatas posteriores conservan esa revisión; no crear copias de unidades del paquete ni elegir una versión antigua sólo por esa nota histórica.

## Estado y siguientes cambios

- rc26 conserva las correcciones de agrupación opcional rc24 y el reparto de accesos por demanda; rc25 está retirada para actualizaciones observer-v4.
- [Operación](docs/OPERATIONS.md) describe las frecuencias y la aceptación por layout. Una versión de identidad no prueba qué recolector o funcionalidades tiene instalado un pipeline heredado.
- [Plan de optimización](docs/MONITORING-PLAN.md): precisión de dashboards, diagnóstico de formatos, exportación por sitio cada cinco minutos y retirada revisada de mediciones duplicadas. Es una propuesta para la siguiente candidata, no una capacidad ya instalada.
- [Semántica de dashboards](docs/DASHBOARDS.md): bloque agregado, frescura y diferencias entre estado reciente e historial.

## Qué detecta

- Sistema y edición CloudLinux; Solo continúa sin LVE, sin inventar ceros.
- LVE funcional en ediciones que lo soportan; un fallo inesperado se informa.
- Métricas de host, procesos/cuentas y tráfico agregado.
- Recomendación de reinicio por actualizaciones: sí, no o desconocido, mediante needs-restarting -r. No consulta KernelCare ni clasifica urgencia.
- WordPress opcional por sitio, con vencimiento y resúmenes de errores sin texto crudo.

El código público no contiene perfiles reales, fuentes de clientes, tokens ni inventarios. Las referencias legacy son código genérico conservado para comprobar migraciones; no son instaladores independientes.

## Descargar y verificar

Descargar `releases/4.0.0-rc26/installer.sh` desde el **commit completo e identificado de la candidata**, que aún no forma parte de `main`. Nunca ejecutar el contenido cambiante de `main` mediante un pipe. El commit y el SHA256 de confianza se comunican junto a cada entrega. Ver [verificación](docs/VERIFICATION.md).

Referencia de integridad rc26: SHA256 `3ec34b076265e00995a00cd6db244fd0732952b3020265de1b4c50689a92474c`. Este digest documentado no sustituye la comprobación independiente de procedencia descrita en verificación.

Ejemplo de flujo, sustituyendo los marcadores por el commit y digest verificados de la entrega:

```bash
wget --https-only -O /root/installer.sh 'https://raw.githubusercontent.com/Duplika/betterstack-installer/COMMIT_VERIFICADO/releases/4.0.0-rc26/installer.sh'
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

Las rutas revisadas y las correcciones acumuladas se detallan en las [notas de versión](docs/RELEASE-NOTES.md); las secciones rc19 y anteriores son historial. Observer-v4 rc6/rc7/rc10/rc12/rc13 incorpora política eficiente y almacenamiento; rc15/rc17 conserva almacenamiento y refresca código. Los dos layouts heredados AlmaLinux conservan el pipeline anterior, comparado con su blueprint y auxiliares oficiales, y actualizan identidad/mantenimiento. No confundir identidad nueva con funcionalidades no instaladas en un pipeline heredado. Versiones/layouts desconocidos o con drift se rechazan, sin reinstalar ni recertificar diferencias.

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

## Historial y rutas especiales

Las correcciones de permisos, perfiles antiguos, recuperación DNF/LVE, almacenamiento y agrupación opcional están descritas en [Operación](docs/OPERATIONS.md) y en las [notas de versión](docs/RELEASE-NOTES.md). Esas notas son historial: no seleccionar una candidata antigua ni repetir una recuperación sin comprobar el layout y el estado real. rc25 está retirada para observer-v4; rc26 conserva su reparto de accesos con marcador de ciclo corregido.

La siguiente optimización está especificada por separado en [MONITORING-PLAN.md](docs/MONITORING-PLAN.md); el cambio de frecuencia por sitio y la eliminación de filesystem duplicado aún no forman parte del agente.
