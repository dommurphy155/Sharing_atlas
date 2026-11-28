"use strict";

/**
 * Informational postinstall only.
 *
 * Deliberately does NOT:
 *   - download source
 *   - create venvs
 *   - install Python deps
 *   - start/stop services
 *   - touch ~/.atlas
 *
 * This keeps `npm install -g` safe under --ignore-scripts, in CI, and as root.
 */

console.log(`
@atlas-ai/cli installed.

  Run:  atlas setup
  Then: atlas start | status | stop | restart

  Docs: https://github.com/dommurphy155/Sharing_atlas
`);
