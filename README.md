# Better Stack installer

Instalador común de Duplika para monitoreo acotado de servidores **AlmaLinux 9 / CloudLinux 9 con cPanel**.

**4.0.0-rc7 es candidata.** Las pruebas locales y de CI no reemplazan el preflight Linux, las pruebas nativas de Vector en destino ni la aceptación de métricas en Better Stack. No hay despliegues masivos automáticos.

## Qué detecta

- Sistema y edición CloudLinux; Solo continúa sin LVE, sin inventar ceros.
- LVE funcional en ediciones que lo soportan; un fallo inesperado se informa.
- Métricas de host, procesos/cuentas y tráfico agregado.
- Recomendación de reinicio por actualizaciones: sí, no o desconocido, mediante needs-restarting -r. No consulta KernelCare ni clasifica urgencia.
- WordPress opcional por sitio, con vencimiento y resúmenes de errores sin texto crudo.

El código público no contiene perfiles reales, fuentes de clientes, tokens ni inventarios. Las referencias legacy son código genérico conservado para comprobar migraciones; no son instaladores independientes.

## Descargar y verificar

Descargar `releases/4.0.0-rc7/installer.sh` desde un **commit completo e identificado**, nunca ejecutar el contenido cambiante de `main` mediante un pipe. El commit y el SHA256 de confianza se comunican junto a cada entrega. Ver [verificación](docs/VERIFICATION.md).

Ejemplo de flujo, sustituyendo los marcadores por el commit y digest verificados de la entrega:

```bash
wget --https-only -O /root/installer.sh 'https://raw.githubusercontent.com/Duplika/betterstack-installer/COMMIT_VERIFICADO/releases/4.0.0-rc7/installer.sh'
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
