# Blog Post: Standardized AI Agents on AgentNode

**Slug:** `standardized-ai-agents-on-agentnode`
**Title:** Standardized AI Agents: Transparent, Verified, Ready to Run
**SEO Title:** Standardized AI Agents on AgentNode — Transparent & Verified
**SEO Description:** AgentNode agents declare their behavior, permissions, and tool access in a standard manifest. Install, inspect, and run — no hidden behavior.
**Excerpt:** Every AgentNode agent now ships with a standardized behavior description, declared permissions, and full transparency. Here's what that means for you.
**Tags:** agents, product, launch, transparency
**Category:** Product Updates

---

## Content (HTML)

```html
<p>Today we're standardizing how AI agents describe themselves on AgentNode. Every agent now ships with a <strong>behavior description</strong>, <strong>declared permissions</strong>, and <strong>full transparency</strong> into what it does and what it needs.</p>

<p>This isn't just a metadata update. It's a fundamental design decision: <strong>you should be able to evaluate an agent before you install it</strong>.</p>

<h2>The Problem with AI Agents Today</h2>

<p>Most AI agent frameworks treat agents as black boxes. You clone a repo, install dependencies, and hope the README is accurate. You don't know what permissions the agent needs, which APIs it calls, or how it behaves until you run it.</p>

<p>That's not good enough for production. If you're running an agent that handles customer data, reviews code, or makes decisions — you need to know what it does <em>before</em> it does it.</p>

<h2>How AgentNode Agents Work</h2>

<p>Every AgentNode agent is packaged with a standardized <code>agentnode.yaml</code> manifest. This manifest declares:</p>

<ul>
  <li><strong>Goal</strong> — What the agent is trying to accomplish</li>
  <li><strong>Agent Behavior</strong> — A human-readable description of the agent's role and approach</li>
  <li><strong>Tier</strong> — Whether the agent uses LLM reasoning only, tools, or external credentials</li>
  <li><strong>Tool Access</strong> — Which tool packs the agent is allowed to use</li>
  <li><strong>Permissions</strong> — Network, filesystem, code execution, and data access levels</li>
  <li><strong>Limits</strong> — Maximum iterations, tool calls, and runtime</li>
  <li><strong>Isolation</strong> — How the agent is sandboxed during execution</li>
</ul>

<p>All of this is visible on the package detail page — before you install anything.</p>

<h2>Agent Tiers</h2>

<p>We classify agents into three tiers based on what they need to run:</p>

<h3>LLM Only</h3>
<p>Pure reasoning agents that use your LLM to think, write, and plan. No external tools, no API calls. Examples: Blog Writer, Newsletter Agent, Report Generator.</p>

<h3>LLM + Tools</h3>
<p>Agents that combine LLM reasoning with AgentNode tool packs. They search the web, extract documents, analyze data — using verified tool packs from the registry. Examples: Deep Research Agent, Code Review Agent, Fact Check Agent.</p>

<h3>LLM + Credentials</h3>
<p>Agents that connect to external services using API keys or OAuth. They interact with your CRM, cloud provider, email, or databases. Examples: CRM Enrichment Agent, Cloud Cost Agent, Deployment Agent.</p>

<h2>What's New</h2>

<h3>Behavior Descriptions for All Agents</h3>
<p>All 30 agents on AgentNode now ship with a standardized <code>system_prompt</code> in their manifest. This is shown on the package page as "Agent Behavior" with a clear "description only" label — so you know it's a description of what the agent does, not necessarily the exact prompt sent to the LLM.</p>

<h3>Input &amp; Output Schemas</h3>
<p>Tool capabilities now display their input and output schemas on the package detail page. You can see exactly what parameters a tool expects and what it returns — like API documentation built into the registry.</p>

<h3>Better Quick Start</h3>
<p>The Quick Start section now uses SDK code provided by the package author instead of generating generic templates. If the author provided specific usage examples, you see those.</p>

<h3>Deprecated Package Visibility</h3>
<p>Deprecated packages are now clearly marked in search results, not just on detail pages. No more accidentally installing a deprecated package.</p>

<h3>Validation on Publish</h3>
<p>When you publish an agent, the validator now checks for a <code>system_prompt</code> and warns if it's missing or too short. This ensures every new agent published to the registry meets the transparency standard.</p>

<h2>The Bigger Picture</h2>

<p>This is part of our ongoing work to make AI agents trustworthy by default. AgentNode already verifies every package before listing (install, import, smoke test). Now we're extending that transparency to agent behavior itself.</p>

<p>The goal is simple: <strong>you should never have to read the source code to understand what an agent does</strong>. The manifest tells you everything.</p>

<h2>Try It</h2>

<p>Browse the full list of agents at <a href="https://agentnode.net/agents">agentnode.net/agents</a>, or install one directly:</p>

<pre><code>agentnode install deep-research-agent</code></pre>

<p>Want to publish your own agent? Check the <a href="https://agentnode.net/docs#agents">agent documentation</a> and <a href="https://agentnode.net/publish">publish page</a>.</p>
```
