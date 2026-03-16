#!/usr/bin/env node
import { Command } from "commander";
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

const program = new Command();

program
  .name("agentnode")
  .description("CLI for AgentNode — discover, resolve, and install AI agent capabilities.")
  .version("0.1.0");

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

program.parse();
