# Seguridad

No publicar credenciales, perfiles de servidores reales, datos de clientes ni logs en issues, pull requests o ejemplos. Para reportar un problema, usar una reproducción ficticia y saneada.

El instalador sólo admite código y layouts revisados. Un destino, configuración o estado desconocido detiene la operación en lugar de improvisar una reparación. Mantiene separación entre el colector de lectura con privilegios y el transporte de Vector sin privilegios.

Las nuevas instalaciones pueden instalar Vector desde el proveedor oficial y ejecutar sus pruebas nativas antes de activar el transporte. Esto modifica componentes del monitoreo, no la configuración de servicios alojados. No se promete reversión universal de instalaciones incompletas.

El token de fuente se introduce oculto o se entrega mediante un archivo privado root/600; no se debe enviar al chat ni almacenar en Git. No hay autoactualización remota, ejecución de instrucciones desde logs, lectura de wp-config.php ni exposición de comandos de procesos ajenos. Los logs enviados por el observador nuevo contienen categorías/conteos, no mensajes crudos de aplicaciones.

Para cambios de código: pruebas, revisión, nueva candidata, ensayo en un host y aceptación de recepción/cobertura antes de promoción. Una build firmada no certifica la recepción de métricas ni la urgencia de un reinicio.
