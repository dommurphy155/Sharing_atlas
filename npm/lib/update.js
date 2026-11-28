"use strict";

/**
 * Phase 4 — update and rollback.
 *
 * Downloads a new version alongside the existing one, bootstraps its venv,
 * health-checks it, then atomically flips the active-version pointer.
 * Never overwrites the active install in place. Never auto-restarts services.
 */

const fs = require("fs");
const path = require("path");
const paths = require("./paths");
const { downloadAndExtract, bootstrapVenv } = require("./install");

function acquireLock() {
  fs.mkdirSync(paths.ATLAS_HOME, { recursive: true });
  try {
    fs.writeFileSync(paths.LOCK_FILE, `${process.pid}.${Date.now()}`, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
  } catch (err) {
    if (err.code === "EEXIST")
      throw new Error(
        `another atlas operation is in progress (lock: ${paths.LOCK_FILE})`,
      );
    throw err;
  }
  return () => {
    try {
      fs.unlinkSync(paths.LOCK_FILE);
    } catch (_) {}
  };
}

async function runUpdate(args = []) {
  const release = require("../package.json").version;
  const active = paths.readActive();

  if (active && active.version === release) {
    console.log(`atlas update: already at ${release}`);
    return;
  }

  const releaseLock = acquireLock();
  try {
    console.log(
      `atlas update: ${active ? active.version : "none"} -> ${release}`,
    );
    const { root, sha256 } = await downloadAndExtract(release);

    const installRoot = path.join(paths.INSTALL_DIR, release);
    if (fs.existsSync(installRoot)) {
      throw new Error(`install dir already exists: ${installRoot}`);
    }
    fs.mkdirSync(path.dirname(installRoot), { recursive: true });
    fs.renameSync(root, installRoot);

    await bootstrapVenv(installRoot);

    // Health-check the new install without starting it: just verify the CLI runs.
    const cli = paths.pythonCli(installRoot);
    const py = paths.venvPython(installRoot);
    const { spawn } = require("child_process");
    const ok = await new Promise((resolve) => {
      const child = spawn(py, [cli, "--version"], {
        stdio: "ignore",
        cwd: installRoot,
        shell: false,
        windowsHide: true,
      });
      child.on("error", () => resolve(false));
      child.on("close", (code) => resolve(code === 0));
    });
    if (!ok)
      throw new Error("new version failed its version check; aborting update");

    paths.writeActive({
      version: release,
      sha256,
      installedAt: new Date().toISOString(),
      repo: `${GITHUB_OWNER}/${GITHUB_REPO}`,
      previous: active ? active.version : null,
    });

    console.log(`atlas update: now at ${release}`);
    if (active)
      console.log(
        `atlas update: previous version kept at ${path.join(paths.INSTALL_DIR, active.version)}`,
      );
    console.log("atlas update: run `atlas restart` to activate.");
  } catch (err) {
    console.error(`atlas update: ${err.message}`);
    process.exit(1);
  } finally {
    releaseLock();
  }
}

module.exports = { runUpdate };
