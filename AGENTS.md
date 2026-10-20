# Public installer maintenance

- Public code only. Never add actual server profiles, hostnames, IPs, ingestion tokens, customer inventories, raw logs or operational history.
- Use only reserved example domains and documentation IP ranges in tests/examples.
- Every code change needs a new candidate version; do not overwrite released artifacts.
- Run `sudo env PYTHONPATH=src/agent python3 scripts/check.py` before publishing.
- Preserve the reviewed legacy files byte-for-byte. They are references, not standalone installers.
- The Vector configuration is unchanged from the legacy reference. Changes require native tests with a compatible Vector before release.
- Installing software or migrating servers is separate from publishing this repository.
- No automatic updates, reboots, live patches or web/PHP/MySQL/security configuration changes.
- Download only a pinned commit/release and verify the independently trusted digest or GitHub build attestation before running as root.
- Never report a candidate as remotely accepted based on local tests or a successful build.
