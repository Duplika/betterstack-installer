# Próxima candidata: monitoreo más preciso y económico

Estado: propuesta de implementación; no cambia el runtime rc26 ni habilita despliegues automáticos. No amplía comprobaciones externas. Cada modificación del agente requiere una nueva candidata, artefactos nuevos y aceptación nativa/remota.

## Orden de trabajo

1. Corregir primero la semántica de los dashboards, sin cambiar alertas ni agentes.
2. Añadir diagnóstico acotado de calidad y duración por componente; investigar formatos reales localmente, sin exportar contenido.
3. Pasar lectura de accesos por sitio y exportación de sus métricas a 3600 segundos, separadas del ciclo global de un minuto; reducir sus familias por defecto.
4. Retirar el colector filesystem de Vector únicamente en layouts que ya tengan la sonda dedicada completa y verificada.
5. Comparar un piloto con baseline y avanzar por lotes después de aceptar cobertura, cadencias, costo y rollback.

## Política propuesta

| Grupo | Vigente en observer-v4 rc26 | Propuesta |
| --- | --- | --- |
| CPU/RAM/carga del host | 60 s | Mantener 60 s |
| Lectura de accesos y detección de 5xx por sitio | 60 s | Cada 3600 s; los 5xx de accesos dejan de detectarse por minuto |
| Calidad de lectura y heartbeat | 60 s | Heartbeat global 60 s; calidad de accesos al realizar la lectura horaria |
| Métricas por sitio | Cinco familias cada 60 s cuando hay observación | Tres familias agregadas cada 3600 s |
| Procesos por usuario y LVE | 300 s | Mantener; perfilar cada componente |
| Capacidad/inodos/RAID | Sonda 60 s y filesystem de Vector en paralelo | Una sola medición de capacidad mediante sonda validada |
| SMART | Hasta una consulta por disco cada 6 h | Mantener; distinguir desconocido de sano |
| Desglose de rutas y agentes HTTP | Clasificación durante lectura | Evaluar como diagnóstico temporal, fuera del camino normal |

### Métricas por sitio, evaluadas por separado

Conservar por defecto:

- `duplika_site_requests_per_second`
- `duplika_site_errors_5xx_per_second`
- `duplika_site_response_bytes_per_second`

Retirar del envío rutinario `duplika_site_dynamic_requests_per_second` y `duplika_site_errors_4xx_per_second`. El segundo puede quedar disponible en diagnóstico temporal con vencimiento si hay un uso concreto. La retirada de métricas no debe eliminar el conteo local necesario para incidentes.

Leer accesos y publicar sus métricas una vez por ventana de 3600 segundos, según el alcance confirmado. La detección de errores globales del servidor y kernel conserva su ciclo de 60 segundos. No ejecutar el lector de accesos ni el clasificador de rutas/UA en los ciclos intermedios.

La tasa debe ser suma de conteos dividida por el tiempo realmente observado; no tomar sólo una muestra instantánea y llamarla promedio horario. Ventanas incompletas, reinicios y saltos de reloj requieren contrato explícito; no emitir ceros de relleno ni repetir datos viejos. La detección y recuperación de incidentes HTTP 5xx basadas en accesos pasan a horarias; los errores que aparezcan en logs globales siguen revisándose cada minuto. Adaptar los gráficos y retirar cualquier promesa de detección por sitio en un minuto.

Los límites rc26 son por ejecución: 8 MiB por ciclo y 2 MiB por archivo. No reutilizarlos sin evaluar el volumen de una hora: pueden descartar tráfico que antes se procesaba minuto a minuto. La siguiente candidata debe elegir un presupuesto horario acotado con lectura en chunks, tiempo/CPU limitados y calidad explícita; si no puede cubrir el volumen, declarar muestreo parcial. No aumentar límites indiscriminadamente ni recuperar historia desde cero. Leer una hora puede concentrar trabajo y procesar los mismos bytes totales: menos ejecuciones y menos muestras no garantizan una reducción proporcional de CPU.

Bajo el supuesto de que los mismos sitios estén activos en todos los ciclos, pasar de cinco familias por minuto a tres cada hora reduce sus muestras de 300 a 3 por hora: 99%. Es un cálculo de cadencia/familias, no una medición de ahorro de CPU, bytes facturados o toda la flota. Sitios esporádicos y la unión de sitios por ventana cambian el ahorro real.

### Eliminar duplicación de capacidad en origen

La sonda dedicada observa montajes reales y deduplica dispositivos; Vector puede ver aliases de montaje y montajes del sandbox. No deduplicar sólo por nombre de métrica ni comparar un tmpfs del sandbox con un volumen real.

La siguiente migración debe verificar cobertura de cada volumen local esperado, inodos cuando sean aplicables y señal de sólo lectura. Tras esa aceptación, retirar `filesystem` de la lista de collectors de `host_metrics`; CPU, memoria, red y métricas de I/O de disco continúan. Eliminar o filtrar después del scrape ahorra envío pero no todo el costo de consulta.

No retirar `filesystem_*` de pipelines heredados sin una migración específica que primero aporte la sonda. Los dashboards conservarán fallback por layout, no enviarán ambas familias permanentemente. Si la sonda falla después, debe alertarse su falta: no recuperar silenciosamente una lectura del sandbox y presentarla como medición equivalente.

Migrar mediante hashes oficiales, pruebas Vector nativas, backup y rollback. Conservar fuente, credenciales, data_dir, buffers, cursores y offsets. No modificar artefactos rc26 ni referencias legacy.

## Alcance de la detección de errores

Hay lectores independientes. El lector de errores globales consulta las rutas de servidor incluidas en `error_logs`; el lector de kernel consulta su journal. El conteo HTTP 5xx por sitio proviene de los accesos canónicos de los dominios reconocidos en `domlog_roots`, no del error log global. La lectura de logs WordPress por sitio es opcional, explícita y con vencimiento; no se recorren todos los sitios buscando sus error_log o debug.log.

El alcance confirmado para la siguiente candidata es: accesos y métricas por sitio cada hora; logs globales/kernel y métricas esenciales cada minuto. Se retira el acceso por minuto incluso si no genera eventos remotos. WordPress por sitio queda como diagnóstico explícito y temporal, no como lectura general por minuto. Los ciclos de LVE/procesos siguen vigentes en cinco minutos hasta decidir su política por separado.

Ver [inventario completo](METRICS-INVENTORY.md) para distinguir medición, envío, logs bajo demanda y propuestas aún no acordadas.

## Diagnóstico y parser

Separar las causas de cobertura parcial mediante contadores por host, sin etiqueta por archivo: formato no reconocido, línea sobredimensionada, archivo inaccesible, rotación, inicialización y bytes omitidos. Mostrar conteo de solicitudes observadas para dimensionar el problema; porcentaje de ciclos completos no es porcentaje de solicitudes procesadas.

La expresión ACCESS de rc26 exige una request line con método, URL y HTTP. Un registro sintético válido del log con request `"-"` y estado 408 no coincide. Esto es una limitación reproducible, no la causa demostrada de todos los eventos reales. Antes de aceptar nuevos formatos, construir fixtures sintéticos equivalentes a las formas observadas localmente, con rutas e identidades ficticias. No enviar líneas crudas, URLs, IPs o user agents a Better Stack.

Clasificar código de estado y bytes con validación estricta incluso si la request line está ausente, cuando el formato de log esté confirmado. La request desconocida no debe inventarse como tráfico dinámico. Formatos ambiguos permanecen como no interpretados. Probar requests sin línea, escapes/comillas, líneas parciales, límite por archivo, rotación y crecimiento concurrente.

## Optimización de CPU y duración

Primero instrumentar duración y CPU por componente: inventario, procesos, LVE, accesos, errores y kernel. Mantener labels en un conjunto fijo. Para fallos, usar códigos de causa acotados (timeout, salida no válida, JSON/esquema) en vez de un RuntimeError indistinto; nunca stdout, argumentos sensibles o mensajes crudos.

En paralelo medir deltas de CPU y memoria del cgroup de colector, sonda, Vector e identidad. Registrar throttling y duración de consultas; una cuota puede explicar demora de pared aunque el uso medio de CPU sea bajo. El máximo RSS de hijos no es memoria simultánea. No subir cuotas, ampliar timeouts ni reducir la lectura de errores para ocultar el costo.

Después de medir, priorizar: quitar clasificación de rutas/UA que no tenga consumidor, evitar trabajo sobre archivos sin datos nuevos, revisar inventario estable y serialización/spool. Cachear sólo con invalidación explícita; no perder altas/bajas de cuentas, cambios de inode o rotaciones. La exportación menos frecuente reduce muestras, pero no elimina por sí sola lectura/parsing cada minuto.

## Piloto y aceptación

Preparar la siguiente candidata desde el commit rc26 fijado, con ruta de upgrade estricta desde rc26 y sin adoptar estados rc25 incompletos. Seleccionar un host con tráfico suficiente y mantener el resto como referencia; los nombres y resultados operativos permanecen fuera del repositorio público.

Comparar ventanas equivalentes de 24 horas y comprobar inicialmente al menos dos ventanas horarias de exportación completas. Un piloto corto demuestra funcionamiento, no ahorro diario estable.

- Recepción reciente de host, colector y sonda; LVE/procesos cada 300 s.
- Errores globales/kernel por minuto; eventos 5xx de accesos y recuperación sólo en ventanas horarias, con conteos y cobertura documentados.
- Calidad por causa sin pérdida adicional; no convertir desconocidos en ceros.
- Muestras y bytes por familia, incluyendo nuevos diagnósticos; no equiparar muestras a facturación.
- CPU total por cgroup, pico de memoria, duración por componente y throttling.
- Cobertura de todos los volúmenes reales y ausencia de filesystem duplicado en la fuente.
- Reinicio, cambio de reloj, rotación, actualización y rollback sin reinicio de offsets ni relectura de historia.

Integrar documentación y código con sus versiones correspondientes. No dar una candidata por aceptada remotamente sólo por pasar CI.
