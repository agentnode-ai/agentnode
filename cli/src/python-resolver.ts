/**
 * Python interpreter detection.
 * Spec §13.3
 */

import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const isWindows = process.platform === "win32";

function tryPython(cmd: string): string | null {
  try {
    const version = execSync(`${cmd} --version`, {
      encoding: "utf-8",
      timeout: 5000,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
    if (version.startsWith("Python 3.")) {
      return cmd;
    }
  } catch {
    // not found
  }
  return null;
}

export function resolvePython(verbose = false): string {
  // 1. VIRTUAL_ENV env var
  const venv = process.env.VIRTUAL_ENV;
  if (venv) {
    const bin = isWindows
      ? join(venv, "Scripts", "python.exe")
      : join(venv, "bin", "python");
    if (existsSync(bin)) {
      if (verbose) console.log(`Using Python from VIRTUAL_ENV: ${bin}`);
      return bin;
    }
  }

  // 2. .venv in current directory
  const localVenv = isWindows
    ? join(process.cwd(), ".venv", "Scripts", "python.exe")
    : join(process.cwd(), ".venv", "bin", "python");
  if (existsSync(localVenv)) {
    if (verbose) console.log(`Using Python from .venv: ${localVenv}`);
    return localVenv;
  }

  // 3. python3
  const py3 = tryPython("python3");
  if (py3) {
    if (verbose) console.log(`Using python3`);
    return py3;
  }

  // 4. python
  const py = tryPython("python");
  if (py) {
    if (verbose) console.log(`Using python`);
    return py;
  }

  throw new Error(
    "No Python interpreter found. " +
    "Please activate a virtual environment or ensure python3 is on PATH."
  );
}

export function getPythonVersion(pythonPath: string): string {
  try {
    return execSync(`${pythonPath} --version`, {
      encoding: "utf-8",
      timeout: 5000,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim().replace("Python ", "");
  } catch {
    return "unknown";
  }
}
