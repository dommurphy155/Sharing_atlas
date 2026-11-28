"use strict";

/**
 * Phase 5 — uninstall.
 *
 * Distinguishes:
 *   - removing the npm package (user runs `npm uninstall -g @atlas-ai/cli`)
 *   - removing Atlas application data (~/.atlas, keys, DB, logs)
 *
 * Requires explicit confirmation before deleting user data.
 */

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const paths = require("./paths");
const { runCapture } = require("./install");

function confirm(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question(question, (answer) => {
      rl.close();
      resolve(/^y(es)?$/i.test(answer.trim()));
    });
  });
}

async function runUninstall(args = []) {
  const active = paths.readActive();
  const installRoot = active
    ? path.join(paths.INSTALL_DIR, active.version)
    : null;

  console.log("atlas uninstall: this removes Atlas application data.");
  console.log(`  install dir: ${paths.INSTALL_DIR}`);
  console.log(
    `  data dir:    ${paths.DATA_DIR}  (keys, .env, dashboard DB, logs)`,
  );
  if (!installRoot) {
    console.log(
      "atlas uninstall: no active installation found; nothing to do.",
    );
    return;
  }

  // Best-effort stop via existing CLI. Never throws.
  const py = paths.venvPython(installRoot);
  const cli = paths.pythonCli(installRoot);
  if (fs.existsSync(py) && fs.existsSync(cli)) {
    console.log("atlas uninstall: stopping proxy (best effort)...");
    await runCapture(py, [cli, "stop"], 30000, { cwd: installRoot });
  }

  const yes = await confirm('  Type "yes" to delete all Atlas data: ');
  if (!yes) {
    console.log("atlas uninstall: cancelled; data preserved.");
    return;
  }

  try {
    fs.rmSync(paths.ATLAS_HOME, { recursive: true, force: true });
  } catch (err) {
    console.error(
      `atlas uninstall: failed to remove ${paths.ATLAS_HOME}: ${err.message}`,
    );
    process.exit(1);
  }

  console.log("atlas uninstall: removed ~/.atlas");
  console.log(
    "atlas uninstall: run `npm uninstall -g @atlas-ai/cli` to remove the npm shim.",
  );
}

module.exports = { runUninstall };
