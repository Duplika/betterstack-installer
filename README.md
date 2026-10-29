# Better Stack installer — candidata rc28

Monitoreo esencial para servidores cPanel con una migración explícita desde **observer-v4 rc26 completo o rc27 esencial, con sonda de almacenamiento**. rc28 es candidata: no implica aceptación remota ni promoción a estable. Los artefactos previos y las referencias legacy se conservan intactos.

rc28 mueve la lectura de I/O a su colector root ya existente, sin elevar privilegios de Vector ni cambiar protecciones del sistema. Separa la frescura del heartbeat de los componentes degradados y conserva diagnósticos de comandos clasificados, sin salida privada.

## Política esencial

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
wget --https-only -O /root/installer-rc28.sh 'https://raw.githubusercontent.com/Duplika/betterstack-installer/COMMIT_VERIFICADO/releases/4.0.0-rc28/installer.sh'
printf '%s  %s\n' 'SHA256_VERIFICADO' /root/installer-rc28.sh | sha256sum --check
```

La candidata está empaquetada en el repositorio; eso no certifica una GitHub Release publicada o una atestación emitida. Ver [procedencia](docs/VERIFICATION.md).

## Piloto sobre rc26 o rc27

```bash
bash /root/installer-rc28.sh preflight --essential-monitoring
bash /root/installer-rc28.sh upgrade --essential-monitoring
bash /root/installer-rc28.sh verify
```

Revisar primero la salida del preflight: exige muestras reales de contadores I/O bajo restricciones estándar del colector y valida historial/schema de CloudLinux, costo de esa consulta, salud/cobertura de almacenamiento y grafo nativo Vector sin aplicar la migración. El upgrade repite las comprobaciones, respalda y revierte ante fallo. Reinicia sólo el transporte del monitor al cambiar el grafo; no reinicia el servidor ni aplicaciones. Conserva fuente, token, buffers, data_dir y offsets.

La opción sólo acepta rc26 observer-v4 o rc27 esencial, con sonda y grafo revisados. No usar para convertir pipelines heredados ni adoptar rc25 incompleto. Las rutas anteriores sin esta opción mantienen su alcance previo y **no activan** la política esencial. No usar una actualización genérica como sustituto de esta selección. No hay despliegues masivos automáticos.

## Diagnóstico de una cuenta

```bash
bash /root/installer-rc28.sh diagnostic-enable --user example --minutes 30 --readers access lve
bash /root/installer-rc28.sh diagnostic-status
bash /root/installer-rc28.sh diagnostic-disable
```

Cuenta exacta, máximo 60 min, activación en el siguiente ciclo. Lectores: access, lve, processes y wordpress. El lector de accesos sólo abre dominios reconocidos de esa cuenta; procesos y LVE sólo exportan sus datos. El estado de diagnóstico es separado de los cursores de la operación anterior y se retira al vencer. No se habilita automáticamente a partir de una alerta.

WordPress sólo admite rutas y vencimientos previamente aprobados en el perfil y sandbox instalado. Si no existen, se rechaza: esta candidata no añade binds ni acceso a nuevos docroots. No se lee wp-config.php, no se ejecuta WordPress ni se habilita debug.

## Aceptación y reversión

Comprobar recepción remota de host/colector/sonda, ausencia de series rutinarias retiradas una vez drenado el transporte, al menos dos ventanas históricas completas y vencimiento del diagnóstico. Comparar 24 h de CPU total por cgroup, memoria, eventos y muestras antes/después. No equiparar muestras o JSON local con bytes facturados.

```bash
bash /root/installer-rc28.sh rollback
```

Restaura la última transacción, sin retroceder offsets. El estado de ventanas tiene IDs deterministas; una interrupción entre escribir spool y confirmar estado puede duplicar un log. Deduplicar por host, window_id y recurso al sumar; no afirmar exactly-once ni recuperación de ventanas perdidas.

[Operación rc28](docs/ESSENTIAL-MONITORING.md) · [Dashboards](docs/DASHBOARDS.md) · [Historial](docs/RELEASE-NOTES.md) · [Seguridad](SECURITY.md)

## Desarrollo

```bash
sudo env PYTHONPATH=src/agent python3 scripts/check.py
python3 scripts/build.py --output out/release
python3 scripts/check_vector.py --vector /ruta/vector
```

Pruebas sintéticas, build reproducible y Vector nativo no sustituyen aceptación del servidor. Configuración, inventarios, logs y credenciales reales permanecen fuera del repositorio público.
