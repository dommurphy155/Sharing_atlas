"use strict";

/**
 * Cross-platform path resolution for the Atlas npm distribution layer.
 *
 * Layout (all user data outside any version directory):
 *
 *   ~/.atlas/
 *     install/<version>/      immutable source tree + per-version .venv
 *     data/                   canonical keys, .env, dashboard DB, logs
 *     runtime.json            active-version pointer (atomic write)
 *
 * Node built-ins only. No external deps.
 */

const os = require("os");
const path = require("path");
const fs = require("fs");

const ATLAS_HOME = path.join(os.homedir(), ".atlas");
const INSTALL_DIR = path.join(ATLAS_HOME, "install");
const DATA_DIR = path.join(ATLAS_HOME, "data");
const RUNTIME_FILE = path.join(ATLAS_HOME, "runtime.json");
const LOCK_FILE = path.join(ATLAS_HOME, "lock");

/** Return the venv python executable path for a given install root. */
function venvPython(installRoot) {
  const isWin = process.platform === "win32";
  return isWin
    ? path.join(installRoot, ".venv", "Scripts", "python.exe")
    : path.join(installRoot, ".venv", "bin", "python");
}

/** Return the existing Python CLI entry point for a given install root. */
function pythonCli(installRoot) {
  return path.join(installRoot, "atlas", "bin", "atlas");
}

/** Read the active-version pointer. Returns null when absent or corrupt. */
function readActive() {
  try {
    const raw = fs.readFileSync(RUNTIME_FILE, "utf8");
    const data = JSON.parse(raw);
    if (!data || typeof data.version !== "string") return null;
    return data;
  } catch (_) {
    return null;
  }
}

/** Atomically write the active-version pointer. */
function writeActive(record) {
  fs.mkdirSync(ATLAS_HOME, { recursive: true });
  const tmp = RUNTIME_FILE + ".tmp." + process.pid;
  fs.writeFileSync(tmp, JSON.stringify(record, null, 2) + "\n", {
    encoding: "utf8",
    mode: 0o600,
  });
  fs.renameSync(tmp, RUNTIME_FILE);
}

/** Resolve the active install root, or null. */
function activeInstallRoot() {
  const active = readActive();
  if (!active) return null;
  return path.join(INSTALL_DIR, active.version);
}

/** Ensure the canonical data directory exists (0700). */
function ensureDataDir() {
  fs.mkdirSync(DATA_DIR, { recursive: true, mode: 0o700 });
  return DATA_DIR;
}

module.exports = {
  ATLAS_HOME,
  INSTALL_DIR,
  DATA_DIR,
  RUNTIME_FILE,
  LOCK_FILE,
  venvPython,
  pythonCli,
  readActive,
  writeActive,
  activeInstallRoot,
  ensureDataDir,
};
