# Approved Playwright Extension CRX

`playwright-extension-0.4.0.crx` is the official Microsoft Playwright Extension
(Apache-2.0, extension ID `mmlmfjhmonkocbjadbfplnigmagldckm`) that
`config/playwright-extension-source.json` approves. The Windows release CI
packages this exact file after `scripts/validate_playwright_extension.py`
checks its size, SHA-256, CRX signature, manifest key and permissions against
that approval.

It is kept in the repository because the Chrome Web Store update service only
serves the newest version (0.5.0 when checked on 2026-11-06), so the approved
0.4.0 can no longer be downloaded from `downloadUrl`. These bytes were taken from the
1.0.20 Windows release ZIP and are identical to the approved SHA-256
`e012b45f6170f627d275588ca1a37dffe5919c2635bbac512f11b74930e63835`.

Changing the extension version requires a new approval and a compatibility
check against the pinned Playwright MCP; do not replace this file on its own.
