"""Seed script: SEO cluster 1 — AgentNode fundamentals.

Usage:
    python -m scripts.seed_blog_cluster1

Requires ADMIN_USER_ID env var (or defaults to first admin in DB).
Creates 5 tutorial posts under the "tutorials" post type.
"""

CLUSTER_1 = [
    # ─────────────────────────────────────────────────────────────
    # Article 1: What Is AgentNode?
    # ─────────────────────────────────────────────────────────────
    {
        "title": "What Is AgentNode? The Complete Guide to AI Agent Skills",
        "slug": "what-is-agentnode-complete-guide-ai-agent-skills",
        "excerpt": "AgentNode is the first registry and platform purpose-built for AI agent capabilities. Learn what agent skills are, how the ANP standard works, and why portable tools matter for the future of AI agents.",
        "seo_title": "What Is AgentNode? Complete Guide to AI Agent Skills",
        "seo_description": "Learn what AgentNode is, how the agent skill registry works, the ANP package format, verification pipeline, and why AI agents need portable capabilities.",
        "tags": ["agentnode", "agent-skills", "ai-agent-capabilities", "agent-tool-registry", "anp-package", "guide"],
        "content_html": """
<h2>The Problem: AI Agents Cannot Share Capabilities</h2>

<p>AI agents are getting remarkably good at reasoning, planning, and breaking down complex tasks. But when it comes to actually <em>doing</em> things in the real world, every agent framework reinvents the wheel. A LangChain developer writes a web scraping tool. A CrewAI developer writes a nearly identical one. An AutoGPT plugin author writes yet another. None of them can use each other's work.</p>

<p>This is the tool fragmentation problem. It exists because there has never been a shared standard for packaging, distributing, and verifying AI agent capabilities. Python has PyPI. JavaScript has npm. But AI agents? Until now, they had nothing.</p>

<p><strong>AgentNode</strong> was built to solve exactly this problem. It is the first registry and platform designed specifically for portable, verified AI agent capabilities — what we call <strong>agent skills</strong>.</p>

<h2>What Is AgentNode?</h2>

<p>AgentNode is three things at once:</p>

<ul>
<li><strong>A registry</strong> — a searchable catalog of agent skills, each with typed schemas, verification scores, and trust badges. Think of it as the npm or PyPI for AI agent tools.</li>
<li><strong>A standard</strong> — the ANP (AgentNode Package) format, a manifest specification that describes what a tool does, what inputs it expects, what outputs it produces, and what permissions it requires.</li>
<li><strong>A verification pipeline</strong> — every package published to AgentNode goes through automated sandbox verification: installation, import checks, smoke tests, and unit tests. The result is a score from 0 to 100 and a trust tier (Gold, Verified, Partial, or Unverified).</li>
</ul>

<p>Together, these three pillars give AI agent developers something they have never had: a way to discover, install, and trust reusable capabilities across any framework.</p>

<h2>Who Is AgentNode For?</h2>

<p>AgentNode serves three overlapping audiences:</p>

<h3>Agent Developers</h3>
<p>If you are building an AI agent — whether with LangChain, CrewAI, AutoGPT, the Model Context Protocol (MCP), or vanilla Python — AgentNode gives you a catalog of pre-built, verified tools you can install in seconds. Instead of writing a PDF parser, a web scraper, or a sentiment analyzer from scratch, you search AgentNode, find a skill that matches your need, and install it with a single SDK call.</p>

<h3>Tool Authors</h3>
<p>If you have built a useful capability — a tool that calls an API, processes data, or interacts with external systems — AgentNode lets you package it once and make it available to every agent framework. You publish using the ANP format, and AgentNode handles verification, discovery, and distribution.</p>

<h3>Platform Builders</h3>
<p>If you are building an agent platform or orchestration layer, AgentNode provides a programmatic API for resolving capabilities at runtime. Your platform can say "I need a tool that does web scraping" and AgentNode returns the best-matching, highest-trust option.</p>

<h2>The ANP Package Format</h2>

<p>At the heart of AgentNode is the ANP (AgentNode Package) format. Every agent skill published to the registry follows this standard. An ANP package is a directory containing:</p>

<ul>
<li><strong>manifest.json</strong> — the package descriptor (manifest_version 0.2), which includes the package name, version, summary, capabilities, permissions, and compatibility metadata.</li>
<li><strong>One or more Python modules</strong> — the actual tool implementations, each exposing a function with typed input/output schemas.</li>
<li><strong>Optional tests</strong> — publisher-provided test files that run during verification.</li>
</ul>

<p>Here is a simplified example of what a manifest looks like:</p>

<pre><code>{
  "manifest_version": "0.2",
  "name": "web-scraper",
  "version": "1.0.0",
  "summary": "Extract structured content from web pages",
  "capabilities": [
    {
      "name": "scrape_page",
      "capability_type": "tool",
      "description": "Scrape and parse a web page into structured text",
      "entrypoint": "tools.scrape:scrape_page",
      "input_schema": {
        "type": "object",
        "properties": {
          "url": {"type": "string", "description": "The URL to scrape"},
          "format": {"type": "string", "enum": ["text", "markdown", "html"]}
        },
        "required": ["url"]
      },
      "output_schema": {
        "type": "object",
        "properties": {
          "content": {"type": "string"},
          "title": {"type": "string"},
          "word_count": {"type": "integer"}
        }
      }
    }
  ],
  "permissions": {
    "network": "external",
    "filesystem": "none",
    "code_execution": "none"
  },
  "compatibility": {
    "frameworks": ["langchain", "crewai", "autogpt", "mcp", "vanilla"],
    "python": ">=3.9"
  }
}</code></pre>

<p>The key insight in this format is that every capability declares <strong>typed input and output schemas</strong> using JSON Schema. This means an AI agent can programmatically understand what a tool expects and what it will return — without reading documentation or guessing.</p>

<h2>How Verification Works</h2>

<p>One of the biggest differences between AgentNode and a traditional package registry is automated verification. When you publish a package, AgentNode does not just store it. It runs the package through a four-step pipeline inside an isolated sandbox container:</p>

<h3>Step 1: Install</h3>
<p>The package and all its dependencies are installed in a clean environment. If installation fails — missing dependencies, version conflicts, broken builds — the package is flagged immediately. This step is worth up to 15 points.</p>

<h3>Step 2: Import</h3>
<p>After installation, AgentNode imports the package and verifies that all declared tool entrypoints are loadable. A package that installs but cannot be imported is not useful to anyone. This step is also worth up to 15 points.</p>

<h3>Step 3: Smoke Test</h3>
<p>AgentNode generates test inputs based on the tool's declared input schema and actually calls the tool. The sandbox runs with <code>--network=none</code> to prevent external calls, so tools that require API credentials or external services are marked as "credential boundary reached" rather than failed. This step is worth up to 25 points — the largest single component.</p>

<h3>Step 4: Unit Tests</h3>
<p>If the publisher provided test files, they are executed. Publisher-provided tests that pass are worth more (15 points) than auto-generated tests (8 points), because they demonstrate the author has validated their own code. Packages without tests still receive a small baseline (3 points) to avoid penalizing otherwise-working tools.</p>

<h3>Scoring and Tiers</h3>
<p>The total score ranges from 0 to 100, with additional points for contract validation, reliability (multi-run consistency), and determinism. The score maps to a tier:</p>

<ul>
<li><strong>Gold (90+)</strong> — fully verified, all checks passed, high reliability</li>
<li><strong>Verified (70-89)</strong> — solid verification with minor gaps</li>
<li><strong>Partial (50-69)</strong> — installs and imports, but limited runtime verification (common for tools requiring API keys)</li>
<li><strong>Unverified (&lt;50)</strong> — significant verification issues</li>
</ul>

<p>Each package page on AgentNode displays its score breakdown, so you can see exactly why a package received its tier. This transparency is deliberate — it lets you make informed trust decisions.</p>

<h2>Cross-Framework Compatibility</h2>

<p>A core design goal of AgentNode is that skills work everywhere. When you install a package using the AgentNode SDK, you get a tool object with a standard interface: a <code>run()</code> method, typed input/output schemas, and metadata. This interface maps naturally to:</p>

<ul>
<li><strong>LangChain</strong> — tools become <code>BaseTool</code> instances</li>
<li><strong>CrewAI</strong> — tools slot into crew task definitions</li>
<li><strong>AutoGPT</strong> — tools register as plugin commands</li>
<li><strong>MCP (Model Context Protocol)</strong> — tools expose as MCP-compliant tool definitions</li>
<li><strong>Vanilla Python</strong> — tools are plain callable objects with schema attributes</li>
</ul>

<p>This portability means that a tool author writes their code once, and it works across every major agent framework without modification.</p>

<h2>Getting Tools Into AgentNode</h2>

<p>There are three ways to create and publish agent skills:</p>

<h3>Write From Scratch</h3>
<p>Create an ANP-compliant package manually, add a manifest.json, and publish via the CLI:</p>
<pre><code>npm install -g agentnode-cli
agentnode publish ./my-tool</code></pre>

<h3>Use the Builder</h3>
<p>Describe what you want in plain language on the AgentNode Builder page, and the platform generates a complete ANP package for you. This is the fastest path from idea to published skill.</p>

<h3>Import Existing Code</h3>
<p>Already have a LangChain tool, an MCP server, an OpenAI function, or a CrewAI tool? The Import page lets you paste your existing code and converts it into an ANP package. Your tool keeps working the way it always did — but now it is portable and discoverable.</p>

<h2>The SDK: Using Skills in Your Agent</h2>

<p>On the consumption side, the AgentNode Python SDK makes it simple to discover, install, and use skills:</p>

<pre><code>pip install agentnode-sdk</code></pre>

<pre><code>from agentnode_sdk import AgentNodeClient, load_tool

client = AgentNodeClient()

# Resolve and install a capability by what it does
client.resolve_and_install(["web-scraping"])

# Load the tool
scraper = load_tool("web-scraper")

# Use it — typed input, typed output
result = scraper.run({"url": "https://example.com", "format": "markdown"})
print(result["content"])</code></pre>

<p>The <code>resolve_and_install</code> method is particularly powerful. You do not need to know the exact package name. You describe the capability you need, and the SDK finds the best-matching, highest-trust package and installs it for you.</p>

<h2>Why This Matters</h2>

<p>The AI agent ecosystem is growing fast. LangChain, CrewAI, AutoGPT, OpenAI's Assistants, Google's ADK, Anthropic's MCP — new frameworks launch regularly. Without a shared tool ecosystem, every framework becomes a silo. Developers duplicate effort. Quality is inconsistent. Trust is impossible to assess.</p>

<p>AgentNode addresses all of these problems by providing a neutral, framework-agnostic registry with built-in verification. As the number of published agent skills grows, the value compounds: every new tool benefits every framework, and every framework's developers contribute back to the shared ecosystem.</p>

<p>If you build AI agents, AgentNode is the tool registry you have been waiting for. <a href="https://agentnode.net/search">Browse the catalog</a> to see what is already available, or <a href="https://agentnode.net/publish">publish your first skill</a> to contribute to the ecosystem.</p>
""",
    },

    # ─────────────────────────────────────────────────────────────
    # Article 2: What Are Agent Skills?
    # ─────────────────────────────────────────────────────────────
    {
        "title": "What Are Agent Skills? Understanding Portable AI Capabilities",
        "slug": "what-are-agent-skills-portable-ai-capabilities",
        "excerpt": "Agent skills are portable, verified AI capabilities packaged in the ANP format. Learn how they differ from regular packages, why portability matters, and how the ANP standard enables cross-framework tool sharing.",
        "seo_title": "What Are Agent Skills? Portable AI Capabilities Explained",
        "seo_description": "Understand what agent skills are, how they differ from regular Python packages, the ANP format standard, and why portable AI capabilities matter.",
        "tags": ["agent-skills", "portable-ai-capabilities", "anp-package", "agent-tool", "ai-agents", "guide"],
        "content_html": """
<h2>Beyond Libraries: What AI Agents Actually Need</h2>

<p>When a developer needs to add PDF parsing to a web application, they install a library — <code>pip install PyPDF2</code> — import it, and write the integration code themselves. The library provides functions. The developer provides the glue.</p>

<p>AI agents work differently. An agent does not just need a library with functions. It needs a <strong>capability</strong> — a self-describing, callable unit of work with a clear contract: what goes in, what comes out, what permissions it requires, and what it can do. The agent needs to understand the tool programmatically, decide when to use it, construct valid inputs, and interpret the outputs — all without human intervention.</p>

<p>This is what an <strong>agent skill</strong> is: a portable, self-describing AI capability that any agent can discover, understand, and invoke.</p>

<h2>Agent Skills vs. Regular Packages</h2>

<p>A regular Python package (on PyPI) or JavaScript package (on npm) is designed for human developers. It has documentation, example code, and an API that humans read and integrate manually. There is no machine-readable contract that says "this function takes a URL string and returns structured markdown text."</p>

<p>An agent skill, by contrast, is designed for both humans and machines. The differences are fundamental:</p>

<ul>
<li><strong>Typed schemas</strong> — every skill declares its input and output types using JSON Schema. An agent can validate inputs before calling and parse outputs without guessing.</li>
<li><strong>Capability declarations</strong> — skills are tagged with what they do ("web-scraping", "text-analysis", "pdf-parsing"), so agents can find tools by function rather than by name.</li>
<li><strong>Permission declarations</strong> — skills declare whether they need network access, filesystem access, code execution, or data access. This lets agents and platforms enforce security policies.</li>
<li><strong>Verification scores</strong> — every skill is tested on publish, with a public score that tells you how thoroughly it has been validated.</li>
<li><strong>Framework agnostic</strong> — a single skill works across LangChain, CrewAI, AutoGPT, MCP, and plain Python. No rewrites needed.</li>
</ul>

<p>Think of it this way: a Python package is a jar of ingredients. An agent skill is a complete recipe card with nutritional labels, allergen warnings, and a quality certification stamp.</p>

<h2>The ANP Package Format</h2>

<p>Agent skills are packaged using the <strong>ANP (AgentNode Package)</strong> format. ANP is a lightweight specification (currently at manifest_version 0.2) that standardizes how agent tools are described and distributed.</p>

<p>An ANP package has three components:</p>

<h3>1. The Manifest</h3>
<p>A <code>manifest.json</code> file that describes the package metadata, its capabilities (tools), compatibility information, and permission requirements. This is the machine-readable contract that makes the tool discoverable and usable by any agent.</p>

<h3>2. The Implementation</h3>
<p>One or more Python modules containing the actual tool logic. Each tool function is referenced by its entrypoint string (e.g., <code>tools.scrape:scrape_page</code>), which tells the runtime exactly where to find the callable.</p>

<h3>3. Optional Tests</h3>
<p>Test files that validate the tool's behavior. Publisher-provided tests carry more weight in the verification score than auto-generated tests, because they demonstrate the author's confidence in their own code.</p>

<h2>Anatomy of a Capability Declaration</h2>

<p>The most important part of the ANP manifest is the capabilities array. Each entry describes one tool within the package. Here is a realistic example for a sentiment analysis tool:</p>

<pre><code>{
  "name": "analyze_sentiment",
  "capability_id": "text-analysis.sentiment",
  "capability_type": "tool",
  "description": "Analyze the sentiment of a text passage and return a score with explanation",
  "entrypoint": "tools.sentiment:analyze",
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {
        "type": "string",
        "description": "The text to analyze",
        "minLength": 1,
        "maxLength": 10000
      },
      "language": {
        "type": "string",
        "description": "ISO 639-1 language code",
        "default": "en"
      }
    },
    "required": ["text"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "score": {
        "type": "number",
        "minimum": -1.0,
        "maximum": 1.0,
        "description": "Sentiment score from -1 (negative) to 1 (positive)"
      },
      "label": {
        "type": "string",
        "enum": ["positive", "negative", "neutral", "mixed"]
      },
      "confidence": {
        "type": "number",
        "minimum": 0,
        "maximum": 1
      }
    }
  }
}</code></pre>

<p>Notice what this gives an agent: it knows the tool's purpose from the description, it knows exactly what input to provide (a text string, optionally with a language code), and it knows exactly what to expect back (a numeric score, a label, and a confidence value). No documentation reading required.</p>

<h2>Multi-Tool Packs</h2>

<p>A single ANP package can contain multiple tools. This is common when a set of related capabilities naturally belong together. For example, a "text-utils" package might expose:</p>

<ul>
<li><code>summarize_text</code> — condense a long document into key points</li>
<li><code>extract_keywords</code> — pull out the most important terms</li>
<li><code>analyze_sentiment</code> — determine the emotional tone</li>
<li><code>detect_language</code> — identify the language of input text</li>
</ul>

<p>Each tool has its own entrypoint, input schema, and output schema. They share the same package dependencies and installation, but function independently. An agent can load one tool or all of them:</p>

<pre><code>from agentnode_sdk import load_tool

# Load a specific tool from a multi-tool pack
summarizer = load_tool("text-utils", tool_name="summarize_text")
keywords = load_tool("text-utils", tool_name="extract_keywords")

result = summarizer.run({"text": long_document, "max_length": 200})</code></pre>

<h2>Portability in Practice</h2>

<p>Portability is the defining feature of agent skills. A tool packaged as an ANP skill works in every supported framework without modification. Here is the same tool used across different agent frameworks:</p>

<h3>Vanilla Python</h3>
<pre><code>from agentnode_sdk import load_tool

scraper = load_tool("web-scraper")
result = scraper.run({"url": "https://example.com"})</code></pre>

<h3>LangChain</h3>
<pre><code>from agentnode_sdk import load_tool

# AgentNode tools are LangChain-compatible
scraper = load_tool("web-scraper")
agent = initialize_agent(
    tools=[scraper],
    llm=llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
)</code></pre>

<h3>CrewAI</h3>
<pre><code>from agentnode_sdk import load_tool
from crewai import Agent, Task

scraper = load_tool("web-scraper")
researcher = Agent(
    role="Web Researcher",
    tools=[scraper],
    goal="Find and extract information from web pages",
)</code></pre>

<p>The tool code does not change. The schema does not change. The behavior does not change. This is what portability means in practice — write once, use everywhere.</p>

<h2>Permission Declarations</h2>

<p>Agent skills declare what system resources they need. This is critical for security. An agent platform can inspect a skill's permissions before installing it and enforce policies like "no tools with filesystem access" or "only tools with verified trust level."</p>

<p>The four permission dimensions are:</p>

<ul>
<li><strong>Network</strong> — none, local, or external. A tool that calls a third-party API declares "external." A tool that only does local computation declares "none."</li>
<li><strong>Filesystem</strong> — none, read, or write. Tools that create or modify files must declare this upfront.</li>
<li><strong>Code Execution</strong> — none or sandboxed. Tools that execute dynamically generated code must declare this.</li>
<li><strong>Data Access</strong> — none, read, or write. Tools that access databases or data stores declare their access patterns.</li>
</ul>

<p>These declarations are verified during the sandbox verification process. A tool that declares "network: none" but attempts to make HTTP calls during smoke testing will be flagged.</p>

<h2>Why Agent Skills Will Define the Next Era of AI</h2>

<p>The shift from libraries to agent skills mirrors a shift that has happened before in software. In the early days of the web, every developer wrote their own HTTP client, their own JSON parser, their own authentication layer. Over time, these became shared packages — and the ecosystem exploded with productivity.</p>

<p>AI agents are at that same inflection point. Today, thousands of developers are writing overlapping tool code in isolated framework silos. Agent skills — portable, verified, self-describing capabilities — are the shared layer that unlocks the next wave of innovation. Instead of building tools, developers can focus on building agents that compose tools in novel ways.</p>

<p>AgentNode and the ANP format provide the foundation. The catalog is growing daily. Every skill that gets published makes every agent framework more capable. That is the network effect of a shared tool ecosystem, and it is just getting started.</p>

<p>Ready to explore? <a href="https://agentnode.net/search">Search the AgentNode catalog</a> to see what agent skills are available today.</p>
""",
    },

    # ─────────────────────────────────────────────────────────────
    # Article 3: Getting Started Tutorial
    # ─────────────────────────────────────────────────────────────
    {
        "title": "Getting Started with AgentNode: Install and Use Your First Agent Skill",
        "slug": "getting-started-agentnode-install-first-agent-skill",
        "excerpt": "A step-by-step tutorial to install the AgentNode SDK, search for agent skills, install your first package, and use load_tool() to run a verified AI capability in Python.",
        "seo_title": "Getting Started with AgentNode SDK: First Agent Skill",
        "seo_description": "Step-by-step AgentNode tutorial: install the Python SDK, search for agent skills, install an ANP package, and use load_tool() to run your first AI capability.",
        "tags": ["agentnode-tutorial", "getting-started", "install-agent-skill", "agentnode-sdk", "python", "tutorial"],
        "content_html": """
<h2>What You Will Build</h2>

<p>By the end of this tutorial, you will have:</p>

<ul>
<li>Installed the AgentNode Python SDK</li>
<li>Searched the registry for agent skills</li>
<li>Installed your first ANP package</li>
<li>Loaded a tool and executed it with typed inputs and outputs</li>
<li>Understood how to integrate agent skills into a real project</li>
</ul>

<p>The entire process takes about five minutes. No account is required for browsing and installing public packages.</p>

<h2>Prerequisites</h2>

<p>You need:</p>

<ul>
<li><strong>Python 3.9 or later</strong> — check with <code>python --version</code></li>
<li><strong>pip</strong> — the standard Python package installer</li>
<li>A terminal (any OS — Linux, macOS, or Windows)</li>
</ul>

<p>Optionally, if you want to use the CLI tools as well:</p>
<ul>
<li><strong>Node.js 18+</strong> and <strong>npm</strong> for the AgentNode CLI</li>
</ul>

<h2>Step 1: Install the AgentNode SDK</h2>

<p>The AgentNode SDK is a standard Python package distributed on PyPI. Install it with pip:</p>

<pre><code>pip install agentnode-sdk</code></pre>

<p>This installs the core library including the <code>AgentNodeClient</code>, the <code>load_tool()</code> function, and all the resolution and installation machinery.</p>

<p>Verify the installation:</p>

<pre><code>python -c "from agentnode_sdk import AgentNodeClient; print('AgentNode SDK installed successfully')"</code></pre>

<p>If you see the success message, you are ready to go.</p>

<h3>Optional: Install the CLI</h3>

<p>The AgentNode CLI provides command-line tools for searching, publishing, and managing packages. It is distributed via npm:</p>

<pre><code>npm install -g agentnode-cli</code></pre>

<p>The CLI is not required for this tutorial, but it is useful for publishing packages and quick searches from the terminal.</p>

<h2>Step 2: Browse the Registry</h2>

<p>Before installing anything, take a moment to explore what is available. You have three options:</p>

<h3>Option A: Web Search</h3>
<p>Visit <a href="https://agentnode.net/search">agentnode.net/search</a> to browse the catalog visually. You can filter by capability type, framework compatibility, verification tier, and trust level. Each result shows the package name, summary, download count, and verification badge.</p>

<h3>Option B: SDK Search</h3>
<p>Use the SDK to search programmatically:</p>

<pre><code>from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()

# Search by keyword
results = client.search("text analysis")
for hit in results:
    print(f"{hit.name} ({hit.verification_tier}) - {hit.summary}")</code></pre>

<h3>Option C: CLI Search</h3>
<p>If you installed the CLI:</p>

<pre><code>agentnode search "web scraping"</code></pre>

<p>All three methods query the same underlying registry and return the same results.</p>

<h2>Step 3: Install a Package</h2>

<p>Let's install a package. For this tutorial, we will use a text analysis tool as our example. There are two ways to install:</p>

<h3>Install by Package Name</h3>

<p>If you know the exact package slug from your search results:</p>

<pre><code>from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()
client.install("word-counter")</code></pre>

<h3>Install by Capability (Resolve and Install)</h3>

<p>If you do not know the exact package name but know what capability you need, use <code>resolve_and_install</code>. This is the recommended approach — it finds the best-matching, highest-trust package for your need:</p>

<pre><code>from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()
client.resolve_and_install(["text-analysis"])</code></pre>

<p>The <code>resolve_and_install</code> method searches the registry for packages that match the requested capability, ranks them by verification score and trust level, and installs the top result. This is powerful for building agents that discover their own tools at runtime.</p>

<h2>Step 4: Load and Use the Tool</h2>

<p>Once a package is installed, you use <code>load_tool()</code> to get a callable tool object:</p>

<pre><code>from agentnode_sdk import load_tool

# Load the tool by package slug
counter = load_tool("word-counter")

# Check what the tool expects
print(counter.name)
print(counter.description)
print(counter.input_schema)
print(counter.output_schema)</code></pre>

<p>The tool object has several useful attributes:</p>

<ul>
<li><code>name</code> — the human-readable tool name</li>
<li><code>description</code> — what the tool does (used by LLMs for tool selection)</li>
<li><code>input_schema</code> — JSON Schema describing the expected input</li>
<li><code>output_schema</code> — JSON Schema describing the return value</li>
<li><code>run(input_dict)</code> — the method to execute the tool</li>
</ul>

<p>Now call the tool:</p>

<pre><code>result = counter.run({
    "text": "AgentNode makes it easy to share AI agent capabilities across frameworks."
})

print(result)
# Output might look like:
# {"word_count": 11, "character_count": 72, "sentence_count": 1}</code></pre>

<p>The input is a dictionary matching the tool's input schema. The output is a dictionary matching the output schema. Everything is typed and validated.</p>

<h2>Step 5: A Complete Working Example</h2>

<p>Let's put it all together in a complete, runnable script that searches for a tool, installs it, and uses it:</p>

<pre><code>"""Complete AgentNode example: search, install, and use an agent skill."""

from agentnode_sdk import AgentNodeClient, load_tool

def main():
    # Initialize the client
    client = AgentNodeClient()

    # Search for text analysis tools
    print("Searching for text analysis tools...")
    results = client.search("text analysis")

    if not results:
        print("No results found.")
        return

    # Show top results
    print(f"Found {len(results)} results:")
    for hit in results[:5]:
        tier = hit.verification_tier or "unscored"
        print(f"  {hit.name} [{tier}] - {hit.summary}")

    # Install the top result
    top_package = results[0].slug
    print(f"\nInstalling {top_package}...")
    client.install(top_package)

    # Load and use the tool
    tool = load_tool(top_package)
    print(f"\nLoaded tool: {tool.name}")
    print(f"Description: {tool.description}")

    # Run with sample input
    sample_text = (
        "Artificial intelligence agents are becoming more capable every day. "
        "They can reason, plan, and execute complex multi-step tasks. "
        "But they need access to tools and capabilities to interact with the real world."
    )

    result = tool.run({"text": sample_text})
    print(f"\nResult: {result}")

if __name__ == "__main__":
    main()</code></pre>

<p>Save this as <code>agentnode_demo.py</code> and run it:</p>

<pre><code>python agentnode_demo.py</code></pre>

<h2>Working with Multi-Tool Packages</h2>

<p>Some packages contain multiple tools. When you call <code>load_tool()</code> on a multi-tool package, you get the primary tool by default. To load a specific tool, pass the <code>tool_name</code> parameter:</p>

<pre><code># Load specific tools from a multi-tool pack
summarizer = load_tool("text-utils", tool_name="summarize_text")
keyword_extractor = load_tool("text-utils", tool_name="extract_keywords")

# Use each independently
summary = summarizer.run({"text": long_document})
keywords = keyword_extractor.run({"text": long_document})</code></pre>

<h2>Understanding Verification Badges</h2>

<p>When browsing or installing packages, pay attention to the verification tier. This tells you how thoroughly the package has been tested:</p>

<ul>
<li><strong>Gold (90-100)</strong> — the highest level. All verification steps passed with high reliability. These tools have been smoke tested with real inputs, produce consistent outputs, and include publisher-provided tests.</li>
<li><strong>Verified (70-89)</strong> — strong verification. The tool installs, imports, and passes most checks, but may have minor gaps like missing custom tests.</li>
<li><strong>Partial (50-69)</strong> — the tool works but could not be fully verified. This is common for tools that require API credentials or external services — the sandbox cannot test them completely, but installation and imports were confirmed.</li>
<li><strong>Unverified (&lt;50)</strong> — significant issues were found during verification. Use with caution.</li>
</ul>

<p>For production agents, prefer Gold and Verified tier tools. For experimentation, Partial tier tools are often perfectly functional — they just need external credentials that the sandbox could not provide.</p>

<h2>Next Steps</h2>

<p>Now that you have installed and used your first agent skill, here are some directions to explore:</p>

<ul>
<li><strong>Browse the full catalog</strong> at <a href="https://agentnode.net/search">agentnode.net/search</a> to discover more tools</li>
<li><strong>Integrate with your agent framework</strong> — AgentNode tools work natively with LangChain, CrewAI, AutoGPT, and MCP</li>
<li><strong>Publish your own skill</strong> — if you have built a useful tool, package it as ANP and share it with the community at <a href="https://agentnode.net/publish">agentnode.net/publish</a></li>
<li><strong>Use the Builder</strong> — describe a tool in plain language at <a href="https://agentnode.net/builder">agentnode.net/builder</a> and let AgentNode generate the code for you</li>
<li><strong>Import existing tools</strong> — already have a LangChain tool or MCP server? <a href="https://agentnode.net/import">Import it</a> into the ANP format</li>
</ul>

<p>The AgentNode ecosystem grows with every new skill published. Whether you are consuming tools or creating them, you are part of building the shared capability layer for AI agents.</p>
""",
    },

    # ─────────────────────────────────────────────────────────────
    # Article 4: Search and Discovery
    # ─────────────────────────────────────────────────────────────
    {
        "title": "How to Search and Discover Agent Skills on AgentNode",
        "slug": "search-discover-agent-skills-agentnode",
        "excerpt": "Learn every way to find agent skills on AgentNode: web search with filters, SDK programmatic search, CLI search, capability resolution, and how to read verification badges and trust levels.",
        "seo_title": "Search and Discover Agent Skills on AgentNode",
        "seo_description": "Master AgentNode search: web filters, SDK programmatic search, CLI queries, capability resolution, verification tiers, and trust badges to find tools.",
        "tags": ["search-agent-skills", "discover-ai-capabilities", "agentnode-search", "resolve-capabilities", "tutorial"],
        "content_html": """
<h2>Finding the Right Tool Matters</h2>

<p>An AI agent is only as good as its tools. A research agent needs reliable web scrapers. A data agent needs trustworthy parsers. A customer service agent needs accurate sentiment analyzers. Finding the right tool — one that is well-built, properly verified, and compatible with your framework — is the first step in building an effective agent.</p>

<p>AgentNode provides multiple ways to search and discover agent skills, each suited to different workflows. This guide covers all of them.</p>

<h2>Web Search: The Visual Catalog</h2>

<p>The most intuitive way to browse agent skills is the web interface at <a href="https://agentnode.net/search">agentnode.net/search</a>. The search page provides a full-text search bar and a set of filters that let you narrow results by multiple dimensions.</p>

<h3>Text Search</h3>
<p>Type any keyword or phrase into the search bar. The search engine matches against package names, summaries, descriptions, capability IDs, and tags. Results are ranked by relevance when you provide a query, or by download count when browsing without a query.</p>

<p>Some effective search strategies:</p>

<ul>
<li><strong>Search by function</strong> — "pdf parsing", "web scraping", "sentiment analysis"</li>
<li><strong>Search by domain</strong> — "finance", "healthcare", "e-commerce"</li>
<li><strong>Search by data type</strong> — "csv", "json", "image", "audio"</li>
<li><strong>Search by integration</strong> — "github", "slack", "google sheets"</li>
</ul>

<h3>Filters</h3>

<p>The filter panel lets you refine results across several dimensions:</p>

<ul>
<li><strong>Package Type</strong> — filter by "tool" (single tool), "pack" (multi-tool), or "connector" (external service integration)</li>
<li><strong>Framework</strong> — show only skills compatible with a specific framework: LangChain, CrewAI, AutoGPT, MCP, or vanilla Python</li>
<li><strong>Verification Tier</strong> — Gold, Verified, Partial, or Unverified. Use this to filter by quality level</li>
<li><strong>Trust Level</strong> — Curated, Trusted, Verified, or Unverified publisher trust. This reflects the publisher's track record, not the individual package score</li>
<li><strong>Publisher</strong> — see all packages from a specific publisher</li>
</ul>

<h3>Sort Options</h3>

<p>Results can be sorted by:</p>

<ul>
<li><strong>Download count (descending)</strong> — the default when browsing without a query. Shows the most popular packages first.</li>
<li><strong>Download count (ascending)</strong> — find hidden gems with few downloads</li>
<li><strong>Published date (newest first)</strong> — see the latest additions to the registry</li>
<li><strong>Name (A-Z or Z-A)</strong> — alphabetical browsing</li>
</ul>

<h3>Reading Search Results</h3>

<p>Each search result card shows:</p>

<ul>
<li>The <strong>package name</strong> and <strong>summary</strong></li>
<li>The <strong>publisher name</strong> with their trust level badge</li>
<li>The <strong>verification tier badge</strong> (Gold, Verified, Partial, Unverified) with the numeric score</li>
<li><strong>Framework compatibility tags</strong> showing which agent frameworks are supported</li>
<li>The <strong>download count</strong></li>
<li>A list of <strong>capability tags</strong></li>
</ul>

<p>Click any result to open the full package detail page, which includes the complete verification breakdown, input/output schemas, installation instructions, usage examples, and version history.</p>

<h2>SDK Search: Programmatic Discovery</h2>

<p>For agents that need to discover tools at runtime, or for developers who prefer code over web interfaces, the AgentNode SDK provides full search capabilities:</p>

<pre><code>from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()

# Basic keyword search
results = client.search("web scraping")

# Inspect results
for hit in results:
    print(f"Package: {hit.slug}")
    print(f"  Name: {hit.name}")
    print(f"  Summary: {hit.summary}")
    print(f"  Score: {hit.verification_score}")
    print(f"  Tier: {hit.verification_tier}")
    print(f"  Trust: {hit.trust_level}")
    print(f"  Downloads: {hit.download_count}")
    print(f"  Frameworks: {', '.join(hit.frameworks)}")
    print()</code></pre>

<h3>Filtered Search</h3>

<p>The SDK search supports the same filters as the web interface:</p>

<pre><code># Search with filters
results = client.search(
    q="data processing",
    framework="langchain",
    verification_tier="gold",
    sort_by="download_count:desc",
    per_page=10,
)</code></pre>

<p>Available filter parameters:</p>

<ul>
<li><code>q</code> — search query string</li>
<li><code>package_type</code> — "tool", "pack", or "connector"</li>
<li><code>capability_id</code> — filter by specific capability ID</li>
<li><code>framework</code> — "langchain", "crewai", "autogpt", "mcp", "vanilla"</li>
<li><code>verification_tier</code> — "gold", "verified", "partial", "unverified"</li>
<li><code>trust_level</code> — "curated", "trusted", "verified", "unverified"</li>
<li><code>publisher_slug</code> — filter by publisher</li>
<li><code>sort_by</code> — "download_count:desc", "download_count:asc", "published_at:desc", "published_at:asc", "name:asc", "name:desc"</li>
</ul>

<h2>Capability Resolution: Let the SDK Decide</h2>

<p>The most powerful discovery mechanism in AgentNode is capability resolution. Instead of searching by keyword and manually picking a package, you tell the SDK what capability you need, and it finds the best match:</p>

<pre><code>from agentnode_sdk import AgentNodeClient

client = AgentNodeClient()

# Resolve by capability description
client.resolve_and_install(["web-scraping"])

# Resolve multiple capabilities at once
client.resolve_and_install([
    "pdf-parsing",
    "text-summarization",
    "sentiment-analysis",
])</code></pre>

<p>Resolution considers multiple factors when ranking matches:</p>

<ul>
<li><strong>Capability match</strong> — how closely the package's declared capabilities match your request</li>
<li><strong>Verification score</strong> — higher-scored packages are preferred</li>
<li><strong>Trust level</strong> — packages from trusted publishers rank higher</li>
<li><strong>Download count</strong> — more popular packages are a signal of quality</li>
</ul>

<p>This mechanism is particularly valuable for autonomous agents that need to acquire tools on the fly. The agent can determine it needs a certain capability, resolve it, install it, and use it — all programmatically without human intervention.</p>

<h2>CLI Search: Quick Terminal Lookups</h2>

<p>The AgentNode CLI provides a fast way to search from the command line:</p>

<pre><code># Basic search
agentnode search "json parser"

# Search with filters
agentnode search "data" --framework langchain --tier gold</code></pre>

<p>The CLI outputs a formatted table with package names, tiers, summaries, and download counts. It is useful for quick lookups when you know roughly what you are looking for and want to find the exact package slug to install.</p>

<h2>Understanding Verification Scores</h2>

<p>Every package on AgentNode has a verification score from 0 to 100, computed from multiple evidence-based components:</p>

<ul>
<li><strong>Install (0-15 points)</strong> — did the package install cleanly with all dependencies?</li>
<li><strong>Import (0-15 points)</strong> — can all declared tool entrypoints be imported?</li>
<li><strong>Smoke Test (0-25 points)</strong> — did the tool produce valid output when called with generated inputs? This is the largest single component because it tests actual runtime behavior.</li>
<li><strong>Unit Tests (0-15 points)</strong> — did the publisher's tests pass? Publisher-provided tests score higher than auto-generated ones.</li>
<li><strong>Contract (0-10 points)</strong> — does the output match the declared output schema?</li>
<li><strong>Reliability (0-10 points)</strong> — does the tool produce consistent results across multiple runs?</li>
<li><strong>Determinism (0-5 points)</strong> — how consistent are the outputs?</li>
<li><strong>Warning deductions</strong> — runtime warnings reduce the score by up to 10 points</li>
</ul>

<p>This breakdown is visible on every package's detail page. When evaluating a tool, look at the breakdown to understand <em>why</em> it received its score, not just the number.</p>

<h2>Understanding Trust Levels</h2>

<p>Trust levels apply to <strong>publishers</strong>, not individual packages. They reflect the publisher's overall track record:</p>

<ul>
<li><strong>Curated</strong> — hand-selected by the AgentNode team. The highest trust level, reserved for official and thoroughly vetted publishers.</li>
<li><strong>Trusted</strong> — publishers with a strong track record of well-verified packages.</li>
<li><strong>Verified</strong> — publishers who have confirmed their identity.</li>
<li><strong>Unverified</strong> — new publishers who have not yet built a track record.</li>
</ul>

<p>A package from a Curated publisher with a Partial verification score might still be more trustworthy than a Gold-scored package from an Unverified publisher. Both signals matter — use them together to make informed decisions.</p>

<h2>Reading a Package Detail Page</h2>

<p>When you click through to a package's detail page, you see a comprehensive view including:</p>

<ul>
<li><strong>Overview</strong> — name, summary, description, publisher info, and trust badges</li>
<li><strong>Capabilities</strong> — each declared tool with its input/output schemas</li>
<li><strong>Installation</strong> — copy-paste commands for SDK, CLI, and manual installation</li>
<li><strong>Verification Panel</strong> — the full score breakdown with per-step details, tier badge, confidence level, and verification environment info</li>
<li><strong>Compatibility</strong> — supported frameworks, Python version requirements, and dependencies</li>
<li><strong>Permissions</strong> — what system access the package requires (network, filesystem, code execution, data access)</li>
<li><strong>Version History</strong> — all published versions with their individual verification statuses</li>
</ul>

<p>The permissions section is especially important for security-conscious deployments. A tool that declares "network: external" and "filesystem: write" needs more scrutiny than one that declares "network: none" and "filesystem: none."</p>

<h2>Tips for Effective Discovery</h2>

<p>Here are practical tips for finding the best tools on AgentNode:</p>

<ul>
<li><strong>Start broad, then filter</strong> — search with a general term, then use tier and framework filters to narrow down.</li>
<li><strong>Check the smoke test reason</strong> — a Partial tier tool with "credential boundary reached" is fundamentally different from one with "import failed." The former probably works fine once you provide API keys.</li>
<li><strong>Look at download counts</strong> — popularity is not a guarantee of quality, but high-download packages have been battle-tested by more users.</li>
<li><strong>Read the score breakdown</strong> — a score of 72 where smoke test passed but tests were missing is very different from 72 where tests passed but smoke test failed.</li>
<li><strong>Check framework compatibility</strong> — if you are building a LangChain agent, filter for LangChain-compatible tools to avoid integration issues.</li>
<li><strong>Use resolve for agents</strong> — if you are building an autonomous agent, use <code>resolve_and_install</code> instead of hardcoding package names. This makes your agent adaptable to new and better tools as they get published.</li>
</ul>

<p>The AgentNode registry is designed to make discovery fast and trust transparent. Whether you search through the web, the SDK, or the CLI, you always have access to the same comprehensive verification data to make informed decisions about which tools to give your agents.</p>
""",
    },

    # ─────────────────────────────────────────────────────────────
    # Article 5: AgentNode vs PyPI vs npm
    # ─────────────────────────────────────────────────────────────
    {
        "title": "AgentNode vs PyPI vs npm: Why AI Agents Need Their Own Registry",
        "slug": "agentnode-vs-pypi-vs-npm-why-ai-agents-need-own-registry",
        "excerpt": "AI agents need more than what PyPI and npm provide. Learn why a dedicated agent tool registry with typed schemas, sandbox verification, and cross-framework portability is essential for the AI agent era.",
        "seo_title": "AgentNode vs PyPI vs npm: Why Agents Need Own Registry",
        "seo_description": "Compare AgentNode with PyPI and npm. Learn why AI agents need a dedicated registry with typed schemas, verification, permissions, and portability.",
        "tags": ["agentnode-vs-pypi", "agent-registry", "ai-tool-registry", "why-agentnode", "comparison", "guide"],
        "content_html": """
<h2>The Registry Question</h2>

<p>If you are building AI agents, you have probably asked this question: "Why can't I just use packages from PyPI or npm for my agent's tools?"</p>

<p>It is a fair question. PyPI has over 500,000 packages. npm has over 2 million. Between them, there is a library for nearly everything. But using a traditional package registry for AI agent tools is like using a parts catalog to build a self-driving car — the parts exist, but nothing tells the car which ones to use, how to use them, or whether they are safe.</p>

<p>This article explains what PyPI and npm were designed for, what AI agents actually need, and how AgentNode fills the gap.</p>

<h2>What PyPI and npm Do Well</h2>

<p>Traditional package registries are foundational infrastructure for modern software development. They solve several critical problems:</p>

<ul>
<li><strong>Distribution</strong> — any developer can publish a package, and any other developer can install it with a single command</li>
<li><strong>Versioning</strong> — semantic versioning and dependency resolution ensure reproducible builds</li>
<li><strong>Discovery</strong> — search and categorization help developers find packages by name or keyword</li>
<li><strong>Community</strong> — download counts, GitHub stars, and ecosystem integration provide social signals of quality</li>
</ul>

<p>These registries have served human developers extraordinarily well for over a decade. But they were designed for a world where a human reads documentation, writes integration code, and makes trust decisions manually. AI agents operate in a fundamentally different paradigm.</p>

<h2>What AI Agents Need (and Traditional Registries Lack)</h2>

<p>An AI agent is not a human developer. It does not read README files. It does not browse GitHub issues. It does not "just know" that a function called <code>parse()</code> probably takes a string and returns a dict. An agent needs explicit, machine-readable contracts at every level. Here is what is missing from PyPI and npm:</p>

<h3>1. Machine-Readable Tool Contracts</h3>

<p>A PyPI package has a <code>setup.py</code> or <code>pyproject.toml</code> that describes its name, version, and dependencies. But it says nothing about what the package <em>does</em> in a way a machine can parse. There is no standard for declaring "this package has a function called <code>scrape_page</code> that takes a URL string and returns a dict with keys <code>content</code>, <code>title</code>, and <code>word_count</code>."</p>

<p>AgentNode packages declare every tool with typed input and output schemas using JSON Schema. An agent can read these schemas, construct valid inputs, and parse outputs without guessing or documentation.</p>

<pre><code># What an agent sees with a PyPI package:
# "requests" — a package exists. No idea what to call or how.

# What an agent sees with an AgentNode package:
# "web-scraper" — has tool "scrape_page"
#   Input: {"url": string (required), "format": string (optional)}
#   Output: {"content": string, "title": string, "word_count": integer}
#   Permissions: network=external, filesystem=none</code></pre>

<h3>2. Capability-Based Discovery</h3>

<p>On PyPI, you search by package name or keyword. If you need a PDF parser, you search "pdf parser" and get dozens of results — PyPDF2, pdfplumber, pdfminer, fitz, camelot, tabula. You, the human, read the docs, compare features, and pick one.</p>

<p>An AI agent cannot do this comparison. It needs capability-based resolution: "I need a tool that can parse PDFs" should return the single best option, already ranked by verification quality and trust. AgentNode's <code>resolve_and_install</code> does exactly this — the agent describes the capability it needs, and the registry returns the optimal match.</p>

<h3>3. Runtime Verification</h3>

<p>PyPI and npm have no built-in verification beyond "does the package upload successfully." A package can have broken imports, missing dependencies, or non-functional code, and it will still be listed. You only discover problems after installing and trying to use it.</p>

<p>AgentNode verifies every package on publish in an isolated sandbox. Four automated steps — install, import, smoke test, and unit tests — produce a score from 0 to 100. The score and full breakdown are visible before you install. You know whether a tool actually works before your agent depends on it.</p>

<h3>4. Permission Declarations</h3>

<p>A PyPI package can do anything Python can do: make network requests, read and write files, execute arbitrary code, access environment variables. There is no mechanism for a package to declare what it needs, or for a platform to enforce restrictions.</p>

<p>AgentNode packages declare their permissions explicitly: network access (none/local/external), filesystem access (none/read/write), code execution (none/sandboxed), and data access (none/read/write). Agent platforms can inspect these declarations and enforce policies. An agent running in a restricted environment can automatically exclude tools that require filesystem write access, for example.</p>

<h3>5. Cross-Framework Portability</h3>

<p>A LangChain tool is not a CrewAI tool. An AutoGPT plugin is not an MCP server. Every framework has its own tool interface, its own way of defining inputs and outputs, and its own discovery mechanism. If you write a tool for LangChain, CrewAI users cannot use it without rewriting the interface.</p>

<p>AgentNode packages use a framework-agnostic standard. A single ANP package works with LangChain, CrewAI, AutoGPT, MCP, and vanilla Python. Write once, use everywhere. The AgentNode SDK handles the framework-specific adapter layer.</p>

<h3>6. Trust Signals Beyond Download Counts</h3>

<p>On PyPI, trust is informal. You check download counts, look at the GitHub repo, maybe read some issues. There is no formal trust model, no verification score, no publisher vetting.</p>

<p>AgentNode provides structured trust at two levels:</p>
<ul>
<li><strong>Package level</strong> — verification score (0-100), tier (Gold/Verified/Partial/Unverified), confidence level, and detailed breakdown</li>
<li><strong>Publisher level</strong> — trust tiers (Curated/Trusted/Verified/Unverified) reflecting the publisher's track record</li>
</ul>

<h2>Side-by-Side Comparison</h2>

<p>Here is a direct comparison across the dimensions that matter for AI agent tools:</p>

<table>
<thead>
<tr><th>Feature</th><th>PyPI / npm</th><th>AgentNode</th></tr>
</thead>
<tbody>
<tr><td>Primary audience</td><td>Human developers</td><td>AI agents and developers</td></tr>
<tr><td>Tool contracts</td><td>None (docs only)</td><td>Typed JSON Schema for all I/O</td></tr>
<tr><td>Discovery model</td><td>Keyword search</td><td>Keyword + capability resolution</td></tr>
<tr><td>Verification</td><td>None</td><td>4-step sandbox pipeline, 0-100 score</td></tr>
<tr><td>Permission model</td><td>None</td><td>Declared: network, fs, exec, data</td></tr>
<tr><td>Framework support</td><td>Framework-specific</td><td>Cross-framework (LC, CrewAI, MCP, etc.)</td></tr>
<tr><td>Trust model</td><td>Informal (downloads, stars)</td><td>Structured (scores, tiers, publisher trust)</td></tr>
<tr><td>Multi-tool packages</td><td>Not standardized</td><td>First-class support</td></tr>
<tr><td>Runtime discovery</td><td>Not possible</td><td>resolve_and_install by capability</td></tr>
<tr><td>Install-time validation</td><td>Dependency check only</td><td>Full runtime verification</td></tr>
</tbody>
</table>

<h2>When to Use What</h2>

<p>AgentNode does not replace PyPI or npm. It serves a different purpose. Here is when to use each:</p>

<h3>Use PyPI / npm when:</h3>
<ul>
<li>You need a general-purpose library (HTTP client, database driver, math library)</li>
<li>A human developer will write the integration code</li>
<li>You do not need machine-readable tool contracts</li>
<li>Framework portability is not a concern</li>
</ul>

<h3>Use AgentNode when:</h3>
<ul>
<li>An AI agent needs to discover and use tools programmatically</li>
<li>You need typed input/output schemas for tool invocation</li>
<li>You want pre-verified tools with transparent quality scores</li>
<li>You need cross-framework portability</li>
<li>Your platform enforces permission policies on tools</li>
<li>You want runtime capability resolution (agent discovers its own tools)</li>
</ul>

<p>In practice, AgentNode packages often <em>use</em> PyPI packages internally. A web scraping agent skill might use <code>beautifulsoup4</code> and <code>httpx</code> from PyPI under the hood. The difference is that the AgentNode package wraps these libraries in a self-describing, verified, portable tool interface that agents can use directly.</p>

<h2>The Composability Advantage</h2>

<p>The real power of a dedicated agent registry emerges from composability. When every tool in an ecosystem has typed schemas, declared permissions, and verified behavior, agents can compose tools reliably.</p>

<p>Consider an agent that needs to:</p>
<ol>
<li>Scrape a web page</li>
<li>Extract text from the HTML</li>
<li>Summarize the text</li>
<li>Translate the summary to Spanish</li>
</ol>

<p>With PyPI packages, the agent developer has to manually find four libraries, write glue code for each, handle type conversions between them, and hope they all work together. With AgentNode skills, the agent can resolve all four capabilities, verify that each tool's output schema is compatible with the next tool's input schema, and chain them together automatically.</p>

<pre><code>from agentnode_sdk import AgentNodeClient, load_tool

client = AgentNodeClient()

# Resolve and install all needed capabilities
client.resolve_and_install([
    "web-scraping",
    "text-extraction",
    "text-summarization",
    "translation",
])

# Load tools
scraper = load_tool("web-scraper")
extractor = load_tool("text-extractor")
summarizer = load_tool("text-summarizer")
translator = load_tool("translator")

# Chain them — each tool's output feeds the next tool's input
page = scraper.run({"url": "https://example.com"})
text = extractor.run({"html": page["content"]})
summary = summarizer.run({"text": text["plain_text"], "max_length": 200})
translated = translator.run({"text": summary["summary"], "target_language": "es"})</code></pre>

<p>This kind of tool composition is where agent skill registries show their true value. The typed schemas make it possible for agents (or agent frameworks) to verify compatibility between tools before running a pipeline, reducing runtime errors and improving reliability.</p>

<h2>The Future: Agent-Native Infrastructure</h2>

<p>PyPI and npm were built for the era of human-written software. AgentNode is built for the era of AI-driven software — where agents discover, evaluate, install, and compose tools with minimal human intervention.</p>

<p>As AI agents become more capable and more autonomous, the need for agent-native infrastructure will only grow. An agent that can reliably find and use verified tools is fundamentally more capable than one that is limited to its built-in code. A registry that understands tool contracts, enforces quality standards, and enables runtime discovery is not a nice-to-have — it is infrastructure.</p>

<p>Traditional registries will continue to serve their purpose for human developers and general-purpose libraries. But for AI agent tools, the requirements are different enough that a dedicated solution is necessary. That is why AgentNode exists.</p>

<p>Explore the registry at <a href="https://agentnode.net/search">agentnode.net/search</a>, or <a href="https://agentnode.net/publish">publish your first agent skill</a> to see the difference firsthand.</p>
""",
    },
]


# ── Seed runner ──

if __name__ == "__main__":
    import asyncio
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from app.database import async_engine, get_session
    from app.blog.models import BlogPost, BlogPostType
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    async def seed():
        SessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
        async with SessionLocal() as session:
            # Find the tutorials post type (or fall back to "post")
            result = await session.execute(
                select(BlogPostType).where(BlogPostType.slug == "tutorials")
            )
            post_type = result.scalar_one_or_none()

            if not post_type:
                result = await session.execute(
                    select(BlogPostType).where(BlogPostType.slug == "post")
                )
                post_type = result.scalar_one_or_none()

            if not post_type:
                print("ERROR: No post type found. Create a 'tutorials' or 'post' type first.")
                return

            # Get admin user id
            admin_id = os.environ.get("ADMIN_USER_ID")
            if not admin_id:
                from app.auth.models import User
                result = await session.execute(
                    select(User).where(User.is_admin == True).limit(1)
                )
                admin = result.scalar_one_or_none()
                if not admin:
                    print("ERROR: No admin user found. Set ADMIN_USER_ID env var.")
                    return
                admin_id = admin.id

            created = 0
            skipped = 0
            for article in CLUSTER_1:
                existing = await session.execute(
                    select(BlogPost).where(BlogPost.slug == article["slug"])
                )
                if existing.scalar_one_or_none():
                    print(f"  SKIP (exists): {article['slug']}")
                    skipped += 1
                    continue

                import math
                import re
                text = re.sub(r"<[^>]+>", " ", article["content_html"])
                words = len(text.split())
                reading_time = max(1, math.ceil(words / 200))

                post = BlogPost(
                    title=article["title"],
                    slug=article["slug"],
                    content_html=article["content_html"],
                    excerpt=article["excerpt"],
                    seo_title=article["seo_title"],
                    seo_description=article["seo_description"],
                    tags=article["tags"],
                    author_id=admin_id,
                    post_type_id=post_type.id,
                    reading_time_min=reading_time,
                    status="draft",
                )
                session.add(post)
                created += 1
                print(f"  CREATE: {article['slug']} (~{words} words, {reading_time} min read)")

            await session.commit()
            print(f"\nDone. Created={created}, Skipped={skipped}")

    asyncio.run(seed())
