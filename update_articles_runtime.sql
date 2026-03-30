BEGIN;

UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'ca7c7396-13ed-4384-b393-05fc2157c589' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '657e2049-e8e8-4970-8fc1-a3c928b49e9d' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '299d165f-7635-42e3-b42e-acfbcfe892af' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '3ce1e3be-9a8f-4b24-96a4-f9fb9d8f2e8c' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'b2a536a4-fb61-413f-8c57-66e15788e129' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'c61e771c-2ff9-4077-ba24-09f96e945dc5' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'b8fd6531-b385-40a8-8aff-692d52955f72' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '59eebdb5-ae47-42a7-bd95-5964f304db3f' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'a91fba48-3262-44a6-8438-e58b510d3197' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '3851398c-c0c3-45c3-93c7-c1dfec811239' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '10905169-6545-43e1-8394-cc9e00147bbb' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '3e419af7-9bf9-4934-b4f7-e9896fa5ec13' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'c536cb23-fdfc-49f4-95f3-533fce24d034' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '7ce8eb79-d243-4ce3-afca-da6116e1dcae' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '761495c6-2c73-4058-8157-b4d1d1b83659' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '757a3a96-9347-4c08-934a-503dcdc2b0b6' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '9e7fc31c-302a-4bb7-a62e-fc114434243b' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '03636138-4425-40e5-aaa1-343229392677' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'dfb36e21-e641-4fee-8138-4a09697070f0' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '48e8be41-ba11-4977-97b6-8e7796d48dae' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '7d6104a5-368c-40ea-9990-f001fbfaa530' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '4ba66842-b247-4f58-8bd0-9cb17ba9f0b8' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'ef511f73-c387-4596-aeac-174b6470d938' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '64d61b02-0f60-4a0b-b077-32cdfa37688e' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '801f6719-e05b-4197-a8fe-3400cbc3adf9' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '4600b56a-aea0-425d-a3e4-4701850ca343' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '58e51176-2292-4296-b9d7-daa0c0b27f3a' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '699b0063-f489-42b5-8c20-175d2800ac90' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'de908098-8891-4b13-aed7-a9b0424e3abe' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '549d15e8-d44e-4928-8979-147d64ee3bb8' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '9390b9b9-0ba4-4947-91b9-3bcd23ca654a' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '1a848c7d-c08b-4ac8-9824-cb7011168fb3' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'f4ac89a0-6c86-4b7e-8276-c31653cc23bd' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '709260b6-7f79-4c87-93f4-c2400f385b19' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'a3617ffc-a885-40d9-9541-316d2409c7f4' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '696fa6a8-7ed4-46b5-b2f9-b9408680f9c2' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '8df0e685-0d3b-4489-a640-da452a57b92a' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'ed34a334-debe-4f03-88fa-03620689271e' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = '25a83dfb-bb67-46c3-b932-2551d42c5026' AND content_html NOT LIKE '%AgentNodeRuntime%';
UPDATE blog_posts SET content_html = content_html || '
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
', updated_at = NOW() WHERE id = 'f0f6b8b6-695c-4695-9421-a443cc373b68' AND content_html NOT LIKE '%AgentNodeRuntime%';

COMMIT;

-- Verify
SELECT COUNT(*) as updated FROM blog_posts WHERE content_html LIKE '%AgentNodeRuntime%';