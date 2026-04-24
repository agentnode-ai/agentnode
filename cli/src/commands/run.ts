/**
 * agentnode run <slug> — install (if needed) and execute a package.
 *
 * For agents: pass --goal to set the agent objective.
 * For tool packs: pass --args as JSON.
 */

import { Command } from "commander";
import chalk from "chalk";
import { execSync, spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

function findPython(): string {
  const candidates = process.platform === "win32"
    ? ["python", "python3", "py"]
    : ["python3", "python"];

  for (const cmd of candidates) {
    try {
      execSync(`${cmd} --version`, { stdio: "ignore" });
      return cmd;
    } catch {
      continue;
    }
  }
  throw new Error("Python 3 not found. Install Python 3.10+ to run packages.");
}

function readLockfile(): Record<string, any> {
  const lockPath = resolve("agentnode.lock");
  if (!existsSync(lockPath)) return { packages: {} };
  try {
    return JSON.parse(readFileSync(lockPath, "utf-8"));
  } catch {
    return { packages: {} };
  }
}

export const runCommand = new Command("run")
  .description("Run an installed package (installs automatically if needed)")
  .argument("<slug>", "Package slug to run")
  .option("--goal <goal>", "Agent goal (for agent packages)")
  .option("--args <json>", "Tool arguments as JSON string")
  .option("--tool <name>", "Tool name (for multi-tool packages)")
  .option("--timeout <seconds>", "Execution timeout in seconds", "180")
  .option("--json", "Output raw JSON result")
  .option("--verbose", "Show detailed execution output")
  .action(async (slug: string, opts) => {
    const isJson = !!opts.json;
    const verbose = !!opts.verbose;
    const timeout = parseInt(opts.timeout, 10) || 180;

    try {
      // 1. Check if installed, auto-install if not
      const lockfile = readLockfile();
      const entry = lockfile.packages?.[slug];

      if (!entry) {
        if (!isJson) {
          console.log(chalk.dim(`Package ${slug} not installed. Installing...`));
        }
        try {
          execSync(`agentnode install ${slug}`, {
            stdio: verbose ? "inherit" : "ignore",
          });
        } catch {
          if (isJson) {
            console.log(JSON.stringify({ success: false, error: `Failed to install ${slug}` }));
          } else {
            console.error(chalk.red(`Failed to install ${slug}. Run 'agentnode install ${slug}' for details.`));
          }
          process.exit(1);
        }
      }

      // 2. Build Python execution script
      const python = findPython();
      const isAgent = entry?.package_type === "agent" ||
        readLockfile().packages?.[slug]?.package_type === "agent";

      let pyArgs = "";
      if (opts.goal) {
        pyArgs += `, goal=${JSON.stringify(opts.goal)}`;
      }
      if (opts.tool) {
        pyArgs += `, tool_name=${JSON.stringify(opts.tool)}`;
      }
      if (opts.args) {
        try {
          const parsed = JSON.parse(opts.args);
          for (const [k, v] of Object.entries(parsed)) {
            pyArgs += `, ${k}=${JSON.stringify(v)}`;
          }
        } catch {
          if (isJson) {
            console.log(JSON.stringify({ success: false, error: "Invalid --args JSON" }));
          } else {
            console.error(chalk.red("Invalid --args JSON. Example: --args '{\"query\": \"hello\"}'"));
          }
          process.exit(1);
        }
      }

      const pyScript = `
import json, sys
from agentnode_sdk.runner import run_tool
result = run_tool(${JSON.stringify(slug)}${pyArgs}, timeout=${timeout}.0)
out = {
    "success": result.success,
    "result": result.result,
    "error": result.error,
    "mode_used": result.mode_used,
    "duration_ms": result.duration_ms,
    "run_id": result.run_id,
}
print(json.dumps(out, default=str))
`;

      // 3. Execute
      if (!isJson) {
        if (isAgent) {
          console.log(chalk.cyan(`Running agent ${chalk.bold(slug)}...`));
          if (opts.goal) {
            console.log(chalk.dim(`Goal: ${opts.goal}`));
          }
        } else {
          console.log(chalk.cyan(`Running ${chalk.bold(slug)}...`));
        }
        console.log();
      }

      const child = spawn(python, ["-c", pyScript], {
        stdio: ["ignore", "pipe", verbose ? "inherit" : "pipe"],
        timeout: (timeout + 10) * 1000,
      });

      let stdout = "";
      child.stdout?.on("data", (data: Buffer) => {
        stdout += data.toString();
      });

      const exitCode = await new Promise<number>((resolve) => {
        child.on("close", (code) => resolve(code ?? 1));
      });

      if (exitCode !== 0 && !stdout.trim()) {
        if (isJson) {
          console.log(JSON.stringify({ success: false, error: `Process exited with code ${exitCode}` }));
        } else {
          console.error(chalk.red(`Execution failed (exit code ${exitCode}).`));
          console.error(chalk.dim("Run with --verbose for details."));
        }
        process.exit(1);
      }

      // 4. Parse and display result
      try {
        const result = JSON.parse(stdout.trim().split("\n").pop()!);

        if (isJson) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          if (result.success) {
            console.log(chalk.green("Success"));
            if (result.duration_ms) {
              console.log(chalk.dim(`Duration: ${(result.duration_ms / 1000).toFixed(1)}s`));
            }
            if (result.run_id) {
              console.log(chalk.dim(`Run ID: ${result.run_id}`));
            }
            console.log();

            // Pretty-print result
            if (typeof result.result === "object" && result.result !== null) {
              for (const [key, value] of Object.entries(result.result as Record<string, unknown>)) {
                if (typeof value === "string" && value.length > 200) {
                  console.log(chalk.bold(`${key}:`));
                  console.log(value);
                  console.log();
                } else {
                  console.log(`${chalk.bold(key)}: ${typeof value === "object" ? JSON.stringify(value, null, 2) : value}`);
                }
              }
            } else if (result.result != null) {
              console.log(result.result);
            }
          } else {
            console.error(chalk.red(`Failed: ${result.error}`));
            if (result.run_id) {
              console.log(chalk.dim(`Run ID: ${result.run_id}`));
              console.log(chalk.dim(`View logs: agentnode runs ${result.run_id}`));
            }
            process.exit(1);
          }
        }
      } catch {
        // Couldn't parse JSON, output raw
        if (isJson) {
          console.log(JSON.stringify({ success: false, error: "Could not parse result", raw: stdout }));
        } else {
          console.log(stdout);
        }
      }
    } catch (err: any) {
      if (isJson) {
        console.log(JSON.stringify({ success: false, error: err.message }));
      } else {
        console.error(chalk.red(err.message));
      }
      process.exit(1);
    }
  });
