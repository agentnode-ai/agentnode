"""Prompt engineering tool that generates optimized prompts from task descriptions."""

from __future__ import annotations


def run(
    task: str,
    technique: str = "chain_of_thought",
    context: str = "",
    examples: list[dict] | None = None,
) -> dict:
    """Generate an optimized prompt for an LLM.

    Args:
        task: Description of the task the prompt should accomplish.
        technique: Prompting technique to use. One of:
            "chain_of_thought", "few_shot", "zero_shot", "tree_of_thought", "react".
        context: Optional background context to include.
        examples: Optional list of dicts with "input" and "output" keys for few-shot.

    Returns:
        dict with prompt, technique, tokens_estimate, tips.
    """
    techniques = {
        "chain_of_thought": _chain_of_thought,
        "few_shot": _few_shot,
        "zero_shot": _zero_shot,
        "tree_of_thought": _tree_of_thought,
        "react": _react,
    }

    if technique not in techniques:
        raise ValueError(f"Unknown technique: {technique}. Choose from {list(techniques)}")

    prompt = techniques[technique](task, context, examples or [])
    tokens_estimate = _estimate_tokens(prompt)
    tips = _get_tips(technique)

    return {
        "prompt": prompt,
        "technique": technique,
        "tokens_estimate": tokens_estimate,
        "tips": tips,
    }


def _build_context_block(context: str) -> str:
    if not context:
        return ""
    return f"\n## Context\n{context}\n"


def _chain_of_thought(task: str, context: str, examples: list[dict]) -> str:
    ctx = _build_context_block(context)
    prompt = f"""You are a helpful and precise assistant.
{ctx}
## Task
{task}

## Instructions
Think through this step-by-step:

Step 1: Understand the core requirements of the task.
Step 2: Identify all inputs, constraints, and edge cases.
Step 3: Break down the solution into logical sub-steps.
Step 4: Work through each sub-step carefully, showing your reasoning.
Step 5: Verify your answer by checking against the original requirements.

Please begin your step-by-step reasoning now."""

    if examples:
        example_block = "\n## Examples for Reference\n"
        for i, ex in enumerate(examples, 1):
            example_block += f"\n### Example {i}\n**Input:** {ex.get('input', '')}\n**Output:** {ex.get('output', '')}\n"
        prompt += example_block

    return prompt


def _few_shot(task: str, context: str, examples: list[dict]) -> str:
    ctx = _build_context_block(context)

    example_block = ""
    if examples:
        example_block = "\n## Examples\n"
        for i, ex in enumerate(examples, 1):
            example_block += f"\n### Example {i}\nInput: {ex.get('input', '')}\nOutput: {ex.get('output', '')}\n"
    else:
        # Provide placeholder guidance when no examples supplied
        example_block = """
## Examples
(No examples were provided. For best results, supply 2-5 representative input/output examples.)
"""

    prompt = f"""You are a helpful and precise assistant.
{ctx}
## Task
{task}
{example_block}
## Your Turn
Now, apply the same pattern demonstrated in the examples above to complete the task. Follow the same format and style as the examples."""

    return prompt


def _zero_shot(task: str, context: str, examples: list[dict]) -> str:
    ctx = _build_context_block(context)
    prompt = f"""You are an expert assistant. Complete the following task directly and precisely.
{ctx}
## Task
{task}

## Requirements
- Be concise and accurate.
- If the task involves generating content, follow best practices for clarity and structure.
- If the task involves analysis, be thorough and evidence-based.
- Provide only the requested output with no unnecessary preamble."""

    return prompt


def _tree_of_thought(task: str, context: str, examples: list[dict]) -> str:
    ctx = _build_context_block(context)
    prompt = f"""You are a brilliant problem solver who explores multiple reasoning paths.
{ctx}
## Task
{task}

## Instructions
Use Tree-of-Thought reasoning to solve this problem:

**Branch 1 - First Approach:**
- Describe your initial approach.
- Work through it step by step.
- Evaluate: What are the strengths and weaknesses?

**Branch 2 - Alternative Approach:**
- Describe a fundamentally different approach.
- Work through it step by step.
- Evaluate: What are the strengths and weaknesses?

**Branch 3 - Creative/Hybrid Approach:**
- Consider combining insights from the previous branches or try a novel angle.
- Work through it step by step.
- Evaluate: What are the strengths and weaknesses?

**Final Synthesis:**
- Compare all branches.
- Select the best approach (or best combination).
- Present your final, refined answer."""

    if examples:
        example_block = "\n## Reference Examples\n"
        for i, ex in enumerate(examples, 1):
            example_block += f"\nExample {i} - Input: {ex.get('input', '')} | Output: {ex.get('output', '')}\n"
        prompt += example_block

    return prompt


def _react(task: str, context: str, examples: list[dict]) -> str:
    ctx = _build_context_block(context)
    prompt = f"""You are an intelligent agent that solves tasks using the ReAct (Reason + Act) framework.
{ctx}
## Task
{task}

## Instructions
Use the following loop until the task is complete:

**Thought 1:** Analyse the task. What do I know? What do I need to find out?
**Action 1:** Describe what action you would take (e.g., search, calculate, retrieve data).
**Observation 1:** Describe what you observe from the action's result.

**Thought 2:** Based on the observation, what is the next step?
**Action 2:** Describe your next action.
**Observation 2:** Describe the result.

(Continue the Thought/Action/Observation cycle as needed.)

**Final Answer:** Once you have enough information, provide a clear, complete answer to the task.

Begin now with Thought 1."""

    if examples:
        example_block = "\n## Reference Examples\n"
        for i, ex in enumerate(examples, 1):
            example_block += f"\nExample {i} - Input: {ex.get('input', '')} | Output: {ex.get('output', '')}\n"
        prompt += example_block

    return prompt


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate (~4 characters per token for English)."""
    return max(1, len(text) // 4)


def _get_tips(technique: str) -> list[str]:
    """Return practical tips for the chosen technique."""
    tips_map = {
        "chain_of_thought": [
            "Works best for math, logic, and multi-step reasoning tasks.",
            "Adding 'Let's think step by step' often improves results.",
            "Pair with few-shot examples for even better accuracy.",
        ],
        "few_shot": [
            "Use 2-5 diverse, representative examples for best results.",
            "Ensure examples cover edge cases and common patterns.",
            "Keep example formatting consistent.",
        ],
        "zero_shot": [
            "Best for straightforward tasks where the model has strong prior knowledge.",
            "Use clear, specific language to reduce ambiguity.",
            "Add constraints (length, format, style) to guide output.",
        ],
        "tree_of_thought": [
            "Ideal for complex problems with multiple valid approaches.",
            "Encourages the model to self-evaluate and compare strategies.",
            "Works well for planning, design, and open-ended problems.",
        ],
        "react": [
            "Best for tasks that require iterative reasoning and information gathering.",
            "Works well when the model needs to simulate tool use or research.",
            "Combine with actual tool calls for agentic workflows.",
        ],
    }
    return tips_map.get(technique, [])
