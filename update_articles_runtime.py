"""
Update all 40 blog articles that reference SDK patterns but don't mention AgentNodeRuntime.
Appends a contextual "LLM Runtime" section to each article's content_html.

Usage: Run this script, then SCP and execute on the server, or pipe SQL via SSH.
"""

# Runtime HTML block — appended to all 40 articles
# Contextual: short, useful, links to docs. Not marketing fluff.

RUNTIME_BLOCK = """
<h2>LLM Runtime: Let the Model Handle It</h2>

<p>If your agent uses OpenAI or Anthropic tool calling, <code>AgentNodeRuntime</code> handles tool registration, system prompt injection, and the tool loop automatically. The LLM discovers, installs, and runs AgentNode capabilities on its own — no hardcoded tool calls needed.</p>

<pre><code>from openai import OpenAI
from agentnode_sdk import AgentNodeRuntime

runtime = AgentNodeRuntime()

result = runtime.run(
    provider="openai",
    client=OpenAI(),
    model="gpt-4o",
    messages=[{"role": "user", "content": "your task here"}],
)
print(result.content)</code></pre>

<p>The Runtime registers 5 meta-tools (<code>agentnode_capabilities</code>, <code>agentnode_search</code>, <code>agentnode_install</code>, <code>agentnode_run</code>, <code>agentnode_acquire</code>) that let the LLM search the registry, install packages, and execute tools autonomously. Works with Anthropic too — just change <code>provider="anthropic"</code> and pass an Anthropic client.</p>

<p>See the <a href="https://agentnode.net/docs#llm-runtime">LLM Runtime documentation</a> for the full API reference, trust levels, and manual tool calling.</p>
"""

# All 40 article IDs that need updating
ARTICLE_IDS = [
    "ca7c7396-13ed-4384-b393-05fc2157c589",  # add-ai-capabilities-python-application-agentnode
    "657e2049-e8e8-4970-8fc1-a3c928b49e9d",  # agentnode-autogen-semantic-kernel-integration
    "299d165f-7635-42e3-b42e-acfbcfe892af",  # agentnode-openai-function-calling-integration
    "3ce1e3be-9a8f-4b24-96a4-f9fb9d8f2e8c",  # agentnode-v040-smart-execution
    "b2a536a4-fb61-413f-8c57-66e15788e129",  # agentnode-vs-pypi-vs-npm-why-ai-agents-need-own-registry
    "c61e771c-2ff9-4077-ba24-09f96e945dc5",  # agent-skills-content-creators-writing-image-video
    "b8fd6531-b385-40a8-8aff-692d52955f72",  # agent-skills-every-ai-developer-should-know
    "59eebdb5-ae47-42a7-bd95-5964f304db3f",  # ai-agent-tools-customer-support-automation
    "a91fba48-3262-44a6-8438-e58b510d3197",  # ai-agent-tools-data-analysis-extract-transform
    "3851398c-c0c3-45c3-93c7-c1dfec811239",  # ai-agent-tools-devops-automate-infrastructure
    "10905169-6545-43e1-8394-cc9e00147bbb",  # ai-agent-tools-ecommerce-product-management
    "3e419af7-9bf9-4934-b4f7-e9896fa5ec13",  # ai-agent-tools-education-personalized-learning
    "c536cb23-fdfc-49f4-95f3-533fce24d034",  # ai-agent-tools-finance-risk-analysis-reporting
    "7ce8eb79-d243-4ce3-afca-da6116e1dcae",  # ai-agent-tools-healthcare-clinical-documentation
    "761495c6-2c73-4058-8157-b4d1d1b83659",  # ai-agent-tools-hr-recruiting-screening-onboarding
    "757a3a96-9347-4c08-934a-503dcdc2b0b6",  # ai-agent-tools-legal-contract-review-compliance
    "9e7fc31c-302a-4bb7-a62e-fc114434243b",  # ai-agent-tools-marketing-automation-campaigns
    "03636138-4425-40e5-aaa1-343229392677",  # ai-agent-tools-sales-prospecting-outreach-automation
    "dfb36e21-e641-4fee-8138-4a09697070f0",  # anp-manifest-reference
    "48e8be41-ba11-4977-97b6-8e7796d48dae",  # best-ai-agent-tools-developers-2026
    "7d6104a5-368c-40ea-9990-f001fbfaa530",  # best-mcp-server-registry-verified-tools
    "4ba66842-b247-4f58-8bd0-9cb17ba9f0b8",  # build-agent-skill-agentnode-builder
    "ef511f73-c387-4596-aeac-174b6470d938",  # build-ai-agent-finds-own-tools-autonomously
    "64d61b02-0f60-4a0b-b077-32cdfa37688e",  # build-code-review-agent-ai-tools
    "801f6719-e05b-4197-a8fe-3400cbc3adf9",  # build-multi-agent-system-shared-tools
    "4600b56a-aea0-425d-a3e4-4701850ca343",  # getting-started-agentnode-install-first-agent-skill
    "58e51176-2292-4296-b9d7-daa0c0b27f3a",  # how-agents-choose-tools-resolution-engine
    "699b0063-f489-42b5-8c20-175d2800ac90",  # import-langchain-tools-agentnode-migration
    "de908098-8891-4b13-aed7-a9b0424e3abe",  # mcp-vs-anp-ai-agent-tool-standards-compared
    "549d15e8-d44e-4928-8979-147d64ee3bb8",  # publishing-first-anp-package-complete-guide
    "9390b9b9-0ba4-4947-91b9-3bcd23ca654a",  # search-discover-agent-skills-agentnode
    "1a848c7d-c08b-4ac8-9824-cb7011168fb3",  # skills-sh-vs-agentnode-agent-skill-directory
    "f4ac89a0-6c86-4b7e-8276-c31653cc23bd",  # using-agentnode-mcp-server-claude-cursor
    "709260b6-7f79-4c87-93f4-c2400f385b19",  # using-agentnode-with-crewai-agent-crews
    "a3617ffc-a885-40d9-9541-316d2409c7f4",  # using-agentnode-with-langchain-integration-guide
    "696fa6a8-7ed4-46b5-b2f9-b9408680f9c2",  # what-are-agent-skills-portable-ai-capabilities
    "8df0e685-0d3b-4489-a640-da452a57b92a",  # what-is-agentnode-complete-guide-ai-agent-skills
    "ed34a334-debe-4f03-88fa-03620689271e",  # what-is-anp-open-standard-ai-agent-capabilities
    "25a83dfb-bb67-46c3-b932-2551d42c5026",  # what-is-anp-open-standard-portable-agent-tools
    "f0f6b8b6-695c-4695-9421-a443cc373b68",  # why-ai-agents-need-verified-tools-and-how-agentnode-solves-it
]


def generate_sql():
    """Generate a single SQL transaction that appends the runtime block to all 40 articles."""
    # Escape single quotes for SQL
    block = RUNTIME_BLOCK.replace("'", "''")

    lines = ["BEGIN;", ""]

    for article_id in ARTICLE_IDS:
        lines.append(f"UPDATE blog_posts SET content_html = content_html || '{block}', updated_at = NOW() WHERE id = '{article_id}' AND content_html NOT LIKE '%AgentNodeRuntime%';")

    lines.append("")
    lines.append("COMMIT;")
    lines.append("")
    lines.append("-- Verify")
    lines.append("SELECT COUNT(*) as updated FROM blog_posts WHERE content_html LIKE '%AgentNodeRuntime%';")

    return "\n".join(lines)


if __name__ == "__main__":
    sql = generate_sql()
    with open("update_articles_runtime.sql", "w", encoding="utf-8") as f:
        f.write(sql)
    print(f"Generated SQL for {len(ARTICLE_IDS)} articles -> update_articles_runtime.sql")
