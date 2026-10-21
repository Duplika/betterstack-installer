# Descarga verificable

## Trust inicial

El instalador se ejecuta como root. Verificar el archivo antes de ejecutarlo; `wget ... | bash` no forma parte del procedimiento.

La entrega incluye un commit completo y un digest SHA256 de confianza comunicado por separado. Descargar el archivo desde ese commit fija el contenido; comparar contra ese digest evita depender de un checksum que un atacante podría sustituir junto con el archivo.

`SHA256SUMS` y el hash interno del payload comprueban integridad, pero no son firmas de autenticidad por sí solos.

## Procedencia firmada de GitHub Actions

El workflow `ci.yml` reconstruye el instalador, exige que coincida con el artefacto comprometido y genera una atestación Sigstore a través de `actions/attest`. La identidad de firma es temporal y está ligada al workflow del repositorio; no hay una clave de firma privada en los servidores ni en los secretos del repositorio.

Con GitHub CLI compatible, verificar el archivo descargado antes de ejecutarlo:

```bash
gh attestation verify /root/installer.sh --repo Duplika/betterstack-installer
```

Revisar la identidad del firmante, el workflow y el commit de origen que muestra la verificación. Una atestación de otro repositorio no es válida para esta entrega. No usar opciones para omitir la comprobación del log de transparencia.

La atestación sólo existe después de que el job de build confiable termine correctamente. No tratar un workflow preparado o un checksum como una atestación ya emitida.

La firma prueba procedencia e integridad, no ausencia de errores ni salud de un servidor. Las descargas de dependencias desde el proveedor oficial tienen su propia cadena de confianza; la atestación del instalador no firma paquetes futuros de terceros.

## Releases

El workflow puede publicar manualmente una candidata como borrador, adjuntar todos los assets y después publicarla. Activar release immutability antes de la primera publicación: el ajuste sólo protege releases futuras. Sin ese ajuste no se debe describir la release como inmutable.

Las comprobaciones criptográficas de releases inmutables son distintas de la atestación del build. Si la release está publicada y marcada inmutable, GitHub CLI compatible también permite:

```bash
gh release verify v4.0.0-rc6 --repo Duplika/betterstack-installer
gh release verify-asset v4.0.0-rc6 /root/installer.sh --repo Duplika/betterstack-installer
```

No mover tags ni reemplazar assets publicados; corregir mediante una nueva versión.

Fuentes oficiales:
- https://github.com/actions/attest
- https://cli.github.com/manual/gh_attestation_verify
- https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes
