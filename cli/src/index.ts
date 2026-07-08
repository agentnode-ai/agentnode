#!/usr/bin/env node
import { Command } from "commander";
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve as pathResolve } from "path";
import { searchCommand } from "./commands/search.js";
import { resolveCommand } from "./commands/resolve.js";
import { installCommand } from "./commands/install.js";
import { infoCommand } from "./commands/info.js";
import { publishCommand } from "./commands/publish.js";
import { loginCommand } from "./commands/login.js";
import { validateCommand } from "./commands/validate.js";
import { listCommand } from "./commands/list.js";
import { auditCommand } from "./commands/audit.js";
import { updateCommand } from "./commands/update.js";
import { rollbackCommand } from "./commands/rollback.js";
import { reportCommand } from "./commands/report.js";
import { recommendCommand } from "./commands/recommend.js";
import { resolveUpgradeCommand } from "./commands/resolve-upgrade.js";
import { policyCheckCommand } from "./commands/policy-check.js";
import { doctorCommand } from "./commands/doctor.js";
import { explainCommand } from "./commands/explain.js";
import { apiKeysCommand } from "./commands/api-keys.js";
import { importCommand } from "./commands/import.js";
import { runsCommand } from "./commands/runs.js";
import { credentialsCommand } from "./commands/credentials.js";
import { authCommand } from "./commands/auth.js";
import { initCommand } from "./commands/init.js";
import { runCommand } from "./commands/run.js";
import { agentCommand } from "./commands/agent.js";

// Read version from package.json so the --version output stays in sync
// with the published npm package instead of drifting against a hardcoded
// literal. Resolved relative to the compiled dist/index.js location.
function getCliVersion(): string {
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    // From dist/index.js → ../package.json, from src/index.ts (tests) → ../package.json
    const pkgPath = pathResolve(here, "..", "package.json");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8")) as { version?: string };
    return pkg.version || "0.0.0";
  } catch {
    return "0.0.0";
  }
}

// DEPRECATION: this legacy TypeScript CLI is frozen. The maintained CLI ships
// with the Python SDK (`pip install agentnode-sdk`) and is the only one with the
// full security model — sandboxed execution, publisher-signature verification,
// Guard policy, MCP, and credentialed tool packs. Print a one-line notice on
// every invocation (stderr, so JSON output on stdout stays clean). Skip it for
// --version / --help to keep those outputs tidy.
function printDeprecationNotice(): void {
  const argv = process.argv.slice(2);
  if (argv.some((a) => a === "-V" || a === "--version" || a === "-h" || a === "--help")) {
    return;
  }
  process.stderr.write(
    "⚠ agentnode-cli (npm) is deprecated and unmaintained. " +
      "Switch to the maintained CLI: pip install agentnode-sdk\n",
  );
}
printDeprecationNotice();

const program = new Command();

program
  .name("agentnode")
  .description(
    "[DEPRECATED] Legacy AgentNode CLI. Use `pip install agentnode-sdk` instead.",
  )
  .version(getCliVersion());

program.addCommand(loginCommand);
program.addCommand(searchCommand);
program.addCommand(resolveCommand);
program.addCommand(installCommand);
program.addCommand(updateCommand);
program.addCommand(rollbackCommand);
program.addCommand(infoCommand);
program.addCommand(publishCommand);
program.addCommand(validateCommand);
program.addCommand(listCommand);
program.addCommand(auditCommand);
program.addCommand(reportCommand);
program.addCommand(recommendCommand);
program.addCommand(resolveUpgradeCommand);
program.addCommand(policyCheckCommand);
program.addCommand(doctorCommand);
program.addCommand(explainCommand);
program.addCommand(apiKeysCommand);
program.addCommand(importCommand);
program.addCommand(runsCommand);
program.addCommand(credentialsCommand);
program.addCommand(authCommand);
program.addCommand(initCommand);
program.addCommand(runCommand);
program.addCommand(agentCommand);

program.parse();
