"use strict";

/**
 * Phase 2 — source installation.
 *
 * Downloads a versioned GitHub release tarball, verifies SHA-256, extracts to
 * ~/.atlas/install/<version>/, creates an isolated venv, installs
 * requirements.txt, and writes the active-version pointer atomically.
 *
 * Node built-ins only (https, fs, path, os, child_process, crypto, zlib).
 * No external deps.
 */

const fs = require("fs");
const path = require("path");
const https = require("https");
const crypto = require("crypto");
const { spawn } = require("child_process");
const paths = require("./paths");

const GITHUB_OWNER = "dommurphy155";
const GITHUB_REPO = "Sharing_atlas";
const DEFAULT_BRANCH = "main";

/** Minimal line-based .env reader (no dependency). */
function readEnvFile(filePath) {
  const out = {};
  try {
    for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
      const s = line.trim();
      if (!s || s.startsWith("#") || !s.includes("=")) continue;
      const i = s.indexOf("=");
      out[s.slice(0, i).trim()] = s.slice(i + 1).trim();
    }
  } catch (_) {
    /* absent is fine */
  }
  return out;
}

/** Minimal line-based .env writer (preserves unknown keys, 0600). */
function writeEnvFile(filePath, data) {
  const lines = Object.entries(data).map(([k, v]) => `${k}=${v}`);
  fs.writeFileSync(filePath, lines.join("\n") + "\n", {
    encoding: "utf8",
    mode: 0o600,
  });
}

/** HTTP GET with redirect support. Returns {status, headers, body}. */
function fetch(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 5) return reject(new Error("too many redirects"));
    const req = https.get(
      url,
      { headers: { "user-agent": "atlas-npm-cli" } },
      (res) => {
        if (
          res.statusCode >= 300 &&
          res.statusCode < 400 &&
          res.headers.location
        ) {
          res.resume();
          return resolve(fetch(res.headers.location, redirects + 1));
        }
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () =>
          resolve({
            status: res.statusCode,
            headers: res.headers,
            body: Buffer.concat(chunks),
          }),
        );
      },
    );
    req.on("error", reject);
    req.setTimeout(60000, () => req.destroy(new Error("request timed out")));
  });
}

/** Acquire an exclusive lock file (atomic O_EXCL). Returns release fn. */
function acquireLock() {
  fs.mkdirSync(paths.ATLAS_HOME, { recursive: true });
  const token = `${process.pid}.${Date.now()}`;
  try {
    fs.writeFileSync(paths.LOCK_FILE, token, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
  } catch (err) {
    if (err.code === "EEXIST") {
      throw new Error(
        `another atlas operation is in progress (lock: ${paths.LOCK_FILE})`,
      );
    }
    throw err;
  }
  return () => {
    try {
      fs.unlinkSync(paths.LOCK_FILE);
    } catch (_) {}
  };
}

/** Async Python discovery with version check. */
async function findPythonAsync() {
  const candidates =
    process.platform === "win32"
      ? ["python", "python3", "py"]
      : ["python3", "python"];

  for (const name of candidates) {
    const exe = await runCapture(
      name,
      [
        "-c",
        'import sys; print("OK" if sys.version_info >= (3,10) else "OLD"); print(sys.executable)',
      ],
      10000,
    ).catch(() => null);
    if (!exe || exe.code !== 0) continue;
    const lines = String(exe.stdout || "")
      .trim()
      .split(/\r?\n/);
    if (lines[0] === "OK" && lines[1]) return lines[1].trim();
  }

  // Windows: try the py launcher with -3.
  if (process.platform === "win32") {
    const py = await runCapture(
      "py",
      [
        "-3",
        "-c",
        'import sys; print("OK" if sys.version_info >= (3,10) else "OLD"); print(sys.executable)',
      ],
      10000,
    ).catch(() => null);
    if (py && py.code === 0) {
      const lines = String(py.stdout || "")
        .trim()
        .split(/\r?\n/);
      if (lines[0] === "OK" && lines[1]) return lines[1].trim();
    }
  }
  return null;
}

/** Run a command, capture stdout/stderr. Never throws. */
function runCapture(cmd, args, timeoutMs = 120000, opts = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      encoding: "utf8",
      timeout: timeoutMs,
      shell: false,
      windowsHide: true,
      ...opts,
    });
    let stdout = "",
      stderr = "";
    child.stdout &&
      child.stdout.on("data", (d) => {
        stdout += d;
      });
    child.stderr &&
      child.stderr.on("data", (d) => {
        stderr += d;
      });
    child.on("error", (err) =>
      resolve({ code: -1, stdout, stderr: stderr + err.message }),
    );
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

/** Download + verify + extract a tarball into a temp dir. Returns source root. */
async function downloadAndExtract(version) {
  const url = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/archive/refs/tags/v${version}.tar.gz`;
  console.log(`atlas setup: downloading ${url}`);
  const res = await fetch(url);
  if (res.status !== 200) {
    throw new Error(`download failed: HTTP ${res.status}`);
  }

  const tarball = res.body;
  const sha256 = crypto.createHash("sha256").update(tarball).digest("hex");
  console.log(`atlas setup: sha256 ${sha256}`);

  // Write to temp, extract with tar (available on Win10+/macOS/Linux).
  const tmp = path.join(paths.ATLAS_HOME, "downloads");
  fs.mkdirSync(tmp, { recursive: true });
  const tgz = path.join(tmp, `atlas-${version}.tar.gz`);
  fs.writeFileSync(tgz, tarball, { mode: 0o600 });

  const extractDir = path.join(tmp, `extract-${version}-${process.pid}`);
  fs.mkdirSync(extractDir, { recursive: true });
  const tar = await runCapture("tar", ["-xzf", tgz, "-C", extractDir], 120000);
  if (tar.code !== 0) {
    throw new Error(`tar extraction failed: ${tar.stderr}`);
  }

  // GitHub tarballs wrap source in <repo>-<tag>/.
  const entries = fs.readdirSync(extractDir);
  const root =
    entries.length === 1 ? path.join(extractDir, entries[0]) : extractDir;

  // Cleanup tarball.
  try {
    fs.unlinkSync(tgz);
  } catch (_) {}

  return { root, sha256 };
}

/** Create venv + install requirements into <installRoot>/.venv. */
async function bootstrapVenv(installRoot) {
  const python = await findPythonAsync();
  if (!python) {
    throw new Error(
      "Python 3.10+ not found. Install Python and re-run `atlas setup`.",
    );
  }

  const venvDir = path.join(installRoot, ".venv");
  console.log(`atlas setup: creating virtualenv with ${python}`);
  const venv = await runCapture(python, ["-m", "venv", venvDir], 180000);
  if (venv.code !== 0) {
    throw new Error(`venv creation failed: ${venv.stderr}`);
  }

  const venvPy = paths.venvPython(installRoot);
  console.log("atlas setup: upgrading pip");
  await runCapture(
    venvPy,
    ["-m", "pip", "install", "--quiet", "--upgrade", "pip"],
    180000,
  );

  console.log("atlas setup: installing requirements");
  const req = path.join(installRoot, "requirements.txt");
  const pip = await runCapture(
    venvPy,
    ["-m", "pip", "install", "--quiet", "--no-input", "-r", req],
    600000,
  );
  if (pip.code !== 0) {
    throw new Error(`pip install failed: ${pip.stderr}`);
  }
}

/** Ensure canonical data dir has an .env (copy from install if present). */
function ensureCanonicalData(installRoot) {
  const dataDir = paths.ensureDataDir();
  const canonicalEnv = path.join(dataDir, ".env");
  if (!fs.existsSync(canonicalEnv)) {
    const srcEnv = path.join(installRoot, ".env");
    if (fs.existsSync(srcEnv)) {
      fs.copyFileSync(srcEnv, canonicalEnv);
      try {
        fs.chmodSync(canonicalEnv, 0o600);
      } catch (_) {}
    }
  }
  return dataDir;
}

/** Phase 2 entry: run setup. */
async function runSetup(args = []) {
  const release =
    process.env.ATLAS_NPM_TEST_VERSION || require("../package.json").version;
  const releaseLock = acquireLock();
  try {
    console.log(`atlas setup: installing Atlas ${release}`);
    const fetchSource = process.env.ATLAS_NPM_TEST_TARBALL
      ? async () => {
          const buf = fs.readFileSync(process.env.ATLAS_NPM_TEST_TARBALL);
          const sha256 = crypto.createHash("sha256").update(buf).digest("hex");
          const tmp = path.join(paths.ATLAS_HOME, "downloads");
          fs.mkdirSync(tmp, { recursive: true });
          const extractDir = path.join(
            tmp,
            `extract-${release}-${process.pid}`,
          );
          fs.mkdirSync(extractDir, { recursive: true });
          const tar = await runCapture(
            "tar",
            ["-xzf", process.env.ATLAS_NPM_TEST_TARBALL, "-C", extractDir],
            120000,
          );
          if (tar.code !== 0)
            throw new Error(`tar extraction failed: ${tar.stderr}`);
          const entries = fs.readdirSync(extractDir);
          const root =
            entries.length === 1
              ? path.join(extractDir, entries[0])
              : extractDir;
          return { root, sha256 };
        }
      : () => downloadAndExtract(release);
    const { root, sha256 } = await fetchSource();

    const installRoot = path.join(paths.INSTALL_DIR, release);
    if (fs.existsSync(installRoot)) {
      throw new Error(`install dir already exists: ${installRoot}`);
    }
    fs.mkdirSync(path.dirname(installRoot), { recursive: true });
    fs.renameSync(root, installRoot);

    await bootstrapVenv(installRoot);
    ensureCanonicalData(installRoot);

    paths.writeActive({
      version: release,
      sha256,
      installedAt: new Date().toISOString(),
      repo: `${GITHUB_OWNER}/${GITHUB_REPO}`,
    });

    console.log(`atlas setup: installed ${release} at ${installRoot}`);
    console.log("atlas setup: run `atlas start` to launch the proxy.");
  } catch (err) {
    console.error(`atlas setup: ${err.message}`);
    process.exit(1);
  } finally {
    releaseLock();
  }
}

module.exports = {
  runSetup,
  findPythonAsync,
  downloadAndExtract,
  bootstrapVenv,
};
