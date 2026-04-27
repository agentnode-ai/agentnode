/**
 * agentnode agent — manage installed agents.
 *
 * Subcommands:
 *   agentnode agent llm                     — show LLM config for all agents
 *   agentnode agent llm <slug> --provider   — set custom LLM for an agent
 *   agentnode agent llm <slug> --reset      — reset to default (invoking LLM)
 */

import { Command } from "commander";
import chalk from "chalk";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createInterface } from "node:readline";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

interface AgentLlmConfig {
  provider: string;
  model: string;
  api_key_env: string;
}

interface AgentConfigFile {
  agents: Record<string, { llm?: AgentLlmConfig }>;
}

const CONFIG_PATH = join(homedir(), ".agentnode", "agent-config.json");

function readAgentConfig(): AgentConfigFile {
  if (!existsSync(CONFIG_PATH)) return { agents: {} };
  try {
    return JSON.parse(readFileSync(CONFIG_PATH, "utf-8"));
  } catch {
    return { agents: {} };
  }
}

function writeAgentConfig(config: AgentConfigFile): void {
  mkdirSync(join(homedir(), ".agentnode"), { recursive: true });
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + "\n", "utf-8");
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

async function askQuestion(prompt: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(prompt, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}

const PROVIDERS: Record<string, { displayName: string; envVar: string; defaultModel: string }> = {
  openai: { displayName: "OpenAI", envVar: "OPENAI_API_KEY", defaultModel: "gpt-4o" },
  anthropic: { displayName: "Anthropic", envVar: "ANTHROPIC_API_KEY", defaultModel: "claude-sonnet-4-6" },
  gemini: { displayName: "Google Gemini", envVar: "GOOGLE_API_KEY", defaultModel: "gemini-2.5-flash" },
  openrouter: { displayName: "OpenRouter", envVar: "OPENROUTER_API_KEY", defaultModel: "anthropic/claude-sonnet-4-6" },
};

const llmCommand = new Command("llm")
  .description("Show or configure LLM settings for agents")
  .argument("[slug]", "Agent slug to configure (omit to list all)")
  .option("--provider <name>", "Set LLM provider (openai, anthropic, gemini, openrouter)")
  .option("--model <model>", "Set model name")
  .option("--api-key <key>", "Set API key (stored in ~/.agentnode/.env)")
  .option("--reset", "Reset agent to use the default (invoking) LLM")
  .option("--json", "Output as JSON")
  .action(async (slug: string | undefined, opts) => {
    const lockfile = readLockfile();
    const config = readAgentConfig();

    // List mode: no slug provided
    if (!slug) {
      const agents = Object.entries(lockfile.packages || {})
        .filter(([, entry]: [string, any]) => entry.package_type === "agent")
        .sort(([a], [b]) => a.localeCompare(b));

      if (agents.length === 0) {
        if (opts.json) {
          console.log(JSON.stringify({ agents: [] }));
        } else {
          console.log(chalk.dim("No agents installed. Run 'agentnode install <agent>' first."));
        }
        return;
      }

      if (opts.json) {
        const result = agents.map(([name, entry]: [string, any]) => {
          const agentConf = entry.agent || {};
          const customLlm = config.agents?.[name]?.llm;
          return {
            slug: name,
            tier: agentConf.tier || null,
            llm: customLlm ? {
              provider: customLlm.provider,
              model: customLlm.model,
              source: "custom",
            } : {
              provider: null,
              model: null,
              source: "invoking_llm",
            },
          };
        });
        console.log(JSON.stringify({ agents: result }, null, 2));
        return;
      }

      console.log(chalk.bold("\nInstalled Agents — LLM Configuration\n"));
      console.log(
        chalk.dim("  " + "Agent".padEnd(35) + "Tier".padEnd(20) + "LLM")
      );
      console.log(chalk.dim("  " + "─".repeat(75)));

      for (const [name, entry] of agents) {
        const agentConf = (entry as any).agent || {};
        const tier = agentConf.tier || "—";
        const customLlm = config.agents?.[name]?.llm;

        const llmDisplay = customLlm
          ? chalk.cyan(`${customLlm.provider}/${customLlm.model}`)
          : chalk.dim("default (invoking LLM)");

        console.log(`  ${chalk.bold(name.padEnd(35))}${tier.padEnd(20)}${llmDisplay}`);
      }

      console.log(chalk.dim(`\n  Configure: agentnode agent llm <slug> --provider openai`));
      console.log(chalk.dim(`  Reset:     agentnode agent llm <slug> --reset\n`));
      return;
    }

    // Configure mode: slug provided
    const entry = lockfile.packages?.[slug];
    if (!entry || entry.package_type !== "agent") {
      console.error(chalk.red(`Agent '${slug}' is not installed.`));
      process.exit(1);
    }

    // Reset
    if (opts.reset) {
      if (config.agents?.[slug]?.llm) {
        delete config.agents[slug].llm;
        if (Object.keys(config.agents[slug]).length === 0) {
          delete config.agents[slug];
        }
        writeAgentConfig(config);
        console.log(chalk.green(`✓ ${slug} reset to default (invoking LLM)`));
      } else {
        console.log(chalk.dim(`${slug} already uses the default LLM.`));
      }
      return;
    }

    // Set provider
    if (opts.provider) {
      const provider = opts.provider.toLowerCase();
      const providerInfo = PROVIDERS[provider];
      if (!providerInfo) {
        console.error(chalk.red(`Unknown provider '${provider}'. Use: ${Object.keys(PROVIDERS).join(", ")}`));
        process.exit(1);
      }

      const model = opts.model || providerInfo.defaultModel;

      // Check for API key
      const envVar = providerInfo.envVar;
      let hasKey = !!process.env[envVar];

      if (!hasKey) {
        // Check ~/.agentnode/.env
        const envPath = join(homedir(), ".agentnode", ".env");
        if (existsSync(envPath)) {
          const envContent = readFileSync(envPath, "utf-8");
          hasKey = envContent.includes(`${envVar}=`);
        }
      }

      if (!hasKey && !opts.apiKey) {
        const key = await askQuestion(
          chalk.cyan(`Enter ${providerInfo.displayName} API key (${envVar}): `)
        );
        if (key) {
          // Save to ~/.agentnode/.env
          const envDir = join(homedir(), ".agentnode");
          mkdirSync(envDir, { recursive: true });
          const envPath = join(envDir, ".env");
          let envContent = "";
          if (existsSync(envPath)) {
            envContent = readFileSync(envPath, "utf-8");
            // Remove existing line for this var
            envContent = envContent
              .split("\n")
              .filter((l) => !l.startsWith(`${envVar}=`))
              .join("\n");
          }
          envContent = envContent.trimEnd() + `\n${envVar}=${key}\n`;
          writeFileSync(envPath, envContent, "utf-8");
          console.log(chalk.green(`  ✓ ${envVar} saved to ~/.agentnode/.env`));
        } else {
          console.log(chalk.yellow(`  No API key provided. Set ${envVar} before running this agent.`));
        }
      } else if (opts.apiKey) {
        const envDir = join(homedir(), ".agentnode");
        mkdirSync(envDir, { recursive: true });
        const envPath = join(envDir, ".env");
        let envContent = existsSync(envPath) ? readFileSync(envPath, "utf-8") : "";
        envContent = envContent
          .split("\n")
          .filter((l) => !l.startsWith(`${envVar}=`))
          .join("\n");
        envContent = envContent.trimEnd() + `\n${envVar}=${opts.apiKey}\n`;
        writeFileSync(envPath, envContent, "utf-8");
        console.log(chalk.green(`  ✓ ${envVar} saved to ~/.agentnode/.env`));
      }

      // Save config
      if (!config.agents[slug]) config.agents[slug] = {};
      config.agents[slug].llm = {
        provider,
        model,
        api_key_env: envVar,
      };
      writeAgentConfig(config);

      console.log(chalk.green(`\n✓ ${slug} configured to use ${providerInfo.displayName} (${model})`));
      console.log(chalk.dim(`  Config saved to ~/.agentnode/agent-config.json`));
      return;
    }

    // Show current config for this agent
    const agentConf = entry.agent || {};
    const customLlm = config.agents?.[slug]?.llm;

    console.log(chalk.bold(`\n${slug}\n`));
    console.log(`  Tier:    ${agentConf.tier || "—"}`);
    console.log(`  Goal:    ${agentConf.goal || "—"}`);
    if (customLlm) {
      console.log(`  LLM:     ${chalk.cyan(`${customLlm.provider}/${customLlm.model}`)}`);
      console.log(`  API Key: ${chalk.dim(customLlm.api_key_env)}`);
    } else {
      console.log(`  LLM:     ${chalk.dim("default (invoking LLM)")}`);
    }
    console.log(chalk.dim(`\n  Set:   agentnode agent llm ${slug} --provider openai`));
    console.log(chalk.dim(`  Reset: agentnode agent llm ${slug} --reset\n`));
  });

export const agentCommand = new Command("agent")
  .description("Manage installed agents")
  .addCommand(llmCommand);
