#!/usr/bin/env node
"use strict";

/**
 * Atlas npm CLI entry point.
 *
 * Responsibilities:
 *   1. Locate the active Atlas installation (~/.atlas/install/<version>).
 *   2. If none exists, direct the user to `atlas setup`.
 *   3. Forward all arguments to the existing Python CLI, untouched.
 *   4. Intercept `update` and `uninstall` (npm-managed lifecycle).
 *
 * No shell string construction. No user input concatenated into commands.
 * Uses child_process.spawn with an argv array.
 */

const { spawn } = require("child_process");
const path = require("path");
const paths = require("../lib/paths");

const VERSION = require("../package.json").version;

function printVersion() {
  const active = paths.readActive();
  const appVersion = active ? active.version : "not installed";
  console.log(`atlas ${VERSION} (app ${appVersion})`);
}

function printHelp() {
  console.log(`
Atlas — Atlas Translation Proxy CLI (npm distribution)

Usage:
  atlas setup              First-time setup (downloads + configures Atlas)
  atlas start              Start the proxy
  atlas stop               Stop the proxy
  atlas restart            Restart the proxy
  atlas status             Show proxy + dashboard status
  atlas logs               Tail proxy logs
  atlas config             Show resolved configuration
  atlas doctor             Diagnose common problems
  atlas update             Update Atlas to the latest version
  atlas uninstall          Remove Atlas (requires confirmation)
  atlas --version          Print version
  atlas --help             Show this help

All other commands are forwarded to the existing Atlas Python CLI.
Run 'atlas <cmd> --help' for command-specific help.
`);
}

function fail(message, code = 1) {
  console.error(`atlas: ${message}`);
  process.exit(code);
}

/** Spawn the Python CLI and forward stdio + exit code. */
function forwardToPython(installRoot, args) {
  const py = paths.venvPython(installRoot);
  const cli = paths.pythonCli(installRoot);

  if (!require("fs").existsSync(py)) {
    fail(
      `virtualenv python not found: ${py}\nRun 'atlas setup' to create the installation.`,
      1,
    );
  }
  if (!require("fs").existsSync(cli)) {
    fail(
      `Atlas CLI not found: ${cli}\nThe installation may be corrupt. Run 'atlas setup' again.`,
      1,
    );
  }

  // Pass the canonical data dir explicitly so the Python side can honour it
  // without modifying application files (proxy.config reads env overrides).
  const env = Object.assign({}, process.env, {
    ATLAS_DATA_DIR: paths.DATA_DIR,
    ATLAS_ENV_FILE: path.join(paths.DATA_DIR, ".env"),
  });

  const child = spawn(py, [cli, ...args], {
    stdio: "inherit",
    cwd: installRoot,
    env,
    shell: false,
    windowsHide: true,
  });

  child.on("error", (err) =>
    fail(`failed to launch Atlas CLI: ${err.message}`, 1),
  );
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code == null ? 1 : code);
  });
}

function main() {
  const args = process.argv.slice(2);

  // npm-level flags handled before anything else.
  if (
    args.length === 0 ||
    args[0] === "--help" ||
    args[0] === "-h" ||
    args[0] === "help"
  ) {
    printHelp();
    process.exit(0);
  }
  if (args[0] === "--version" || args[0] === "-v") {
    printVersion();
    process.exit(0);
  }

  const cmd = args[0];

  // npm-managed lifecycle commands.
  if (cmd === "setup") {
    require("../lib/install").runSetup(args.slice(1));
    return;
  }
  if (cmd === "update") {
    require("../lib/update").runUpdate(args.slice(1));
    return;
  }
  if (cmd === "uninstall") {
    require("../lib/uninstall").runUninstall(args.slice(1));
    return;
  }

  // Everything else forwards to the active Python CLI.
  const installRoot = paths.activeInstallRoot();
  if (!installRoot) {
    console.log("Atlas is not installed yet. Run: atlas setup");
    process.exit(0);
  }
  forwardToPython(installRoot, args);
}

main();
