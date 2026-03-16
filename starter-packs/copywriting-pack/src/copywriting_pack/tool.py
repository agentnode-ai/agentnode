"""Template-based copywriting generator using proven marketing frameworks."""

from __future__ import annotations

import textwrap


# ---------------------------------------------------------------------------
# Framework templates
# ---------------------------------------------------------------------------

def _aida(product: str, audience: str, tone: str) -> dict:
    """AIDA: Attention - Interest - Desire - Action."""
    aud = f" for {audience}" if audience else ""
    headline = f"Discover {product} -- The Smarter Way to Get Results"
    body = textwrap.dedent(f"""\
        **Attention**
        Tired of settling for less? {product} is here to change the game{aud}.

        **Interest**
        {product} combines cutting-edge innovation with effortless usability. \
Whether you're a first-time user or a seasoned pro, you'll notice the \
difference from day one.

        **Desire**
        Imagine having a solution that saves you time, reduces friction, and \
delivers real, measurable results. {product} makes that a reality -- \
trusted by thousands who refuse to compromise.

        **Action**
        Don't wait. Try {product} today and see why everyone is talking about it.""")
    cta = f"Get Started with {product} Now"
    return {"headline": headline, "body": body, "cta": cta, "framework": "aida"}


def _pas(product: str, audience: str, tone: str) -> dict:
    """PAS: Problem - Agitate - Solution."""
    aud = audience if audience else "people like you"
    headline = f"Still Struggling? {product} Has the Answer"
    body = textwrap.dedent(f"""\
        **Problem**
        {aud.capitalize()} face the same frustrating challenge every day: wasted \
time, wasted effort, and solutions that just don't deliver.

        **Agitate**
        Every day you wait, the problem gets worse. Competitors move ahead, \
opportunities slip away, and the cost of doing nothing keeps climbing. \
You deserve better.

        **Solution**
        {product} was built to solve exactly this. It's fast, reliable, and \
designed with {aud} in mind. Stop struggling and start winning.""")
    cta = f"Solve It with {product}"
    return {"headline": headline, "body": body, "cta": cta, "framework": "pas"}


def _bab(product: str, audience: str, tone: str) -> dict:
    """BAB: Before - After - Bridge."""
    aud = audience if audience else "your workflow"
    headline = f"Transform {aud.capitalize()} with {product}"
    body = textwrap.dedent(f"""\
        **Before**
        Before {product}, {aud} meant juggling clunky tools, manual \
processes, and constant frustration. Progress was slow and results were \
unpredictable.

        **After**
        Now picture this: a streamlined experience where everything just works. \
Tasks that took hours now take minutes. Results are consistent, reliable, \
and impressive.

        **Bridge**
        {product} is the bridge that takes you from where you are to where \
you want to be. It's the upgrade {aud} has been waiting for.""")
    cta = f"Start Your Transformation with {product}"
    return {"headline": headline, "body": body, "cta": cta, "framework": "bab"}


def _fab(product: str, audience: str, tone: str) -> dict:
    """FAB: Features - Advantages - Benefits."""
    aud = f" for {audience}" if audience else ""
    headline = f"Why {product} Stands Out From the Rest"
    body = textwrap.dedent(f"""\
        **Features**
        {product} comes packed with powerful capabilities{aud}: intuitive \
interface, lightning-fast performance, seamless integrations, and \
enterprise-grade reliability.

        **Advantages**
        Unlike other solutions, {product} is designed from the ground up for \
simplicity without sacrificing power. Setup takes minutes, not days. \
Updates are automatic. Support is always available.

        **Benefits**
        The bottom line? You save time, reduce costs, and achieve better \
outcomes. {product} isn't just a tool -- it's your competitive advantage.""")
    cta = f"Experience {product} Today"
    return {"headline": headline, "body": body, "cta": cta, "framework": "fab"}


def _four_ps(product: str, audience: str, tone: str) -> dict:
    """4Ps: Promise - Picture - Proof - Push."""
    aud = audience if audience else "users"
    headline = f"{product}: The Promise of Better Results, Delivered"
    body = textwrap.dedent(f"""\
        **Promise**
        {product} promises to make your life easier, your work faster, and \
your results better. That's not marketing fluff -- that's a guarantee.

        **Picture**
        Imagine finishing your work in half the time. Imagine impressing \
stakeholders with flawless output. Imagine having a tool that feels like \
it was custom-built for {aud}.

        **Proof**
        Thousands of {aud} already trust {product} to deliver. With a proven \
track record, consistent five-star reviews, and industry recognition, the \
results speak for themselves.

        **Push**
        There's never been a better time to make the switch. Join the \
{aud} who already know -- {product} is the real deal.""")
    cta = f"Join Thousands Using {product}"
    return {"headline": headline, "body": body, "cta": cta, "framework": "4ps"}


_FRAMEWORKS = {
    "aida": _aida,
    "pas": _pas,
    "bab": _bab,
    "fab": _fab,
    "4ps": _four_ps,
}


# ---------------------------------------------------------------------------
# Tone post-processing
# ---------------------------------------------------------------------------

_TONE_ADJUSTMENTS: dict[str, dict[str, str]] = {
    "persuasive": {},  # default -- templates are already persuasive
    "professional": {"!": "."},
    "casual": {"--": "-"},
    "humorous": {},
    "urgent": {},
}


def _apply_tone(text: str, tone: str) -> str:
    """Apply light tone adjustments to text."""
    tone = tone.lower().strip()
    if tone == "urgent":
        # Add urgency markers
        text = text.replace("Don't wait.", "Act NOW -- time is running out.")
        text = text.replace("There's never been a better time", "This offer won't last forever. NOW is the time")
    if tone == "casual":
        text = text.replace("Discover", "Check out")
        text = text.replace("Imagine", "Picture this:")
        text = text.replace("The bottom line?", "Here's the deal:")
    return text


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    product: str,
    audience: str = "",
    framework: str = "aida",
    tone: str = "persuasive",
) -> dict:
    """Generate marketing copy using a proven copywriting framework.

    Args:
        product: The product or service name/description.
        audience: Target audience description (optional).
        framework: Copywriting framework -- 'aida', 'pas', 'bab', 'fab', or '4ps'.
        tone: Writing tone -- 'persuasive', 'professional', 'casual', 'humorous', 'urgent'.

    Returns:
        dict with keys: headline, body, cta, framework.
    """
    framework = framework.lower().strip()
    if framework not in _FRAMEWORKS:
        framework = "aida"

    result = _FRAMEWORKS[framework](product, audience, tone)

    # Apply tone adjustments
    result["headline"] = _apply_tone(result["headline"], tone)
    result["body"] = _apply_tone(result["body"], tone)
    result["cta"] = _apply_tone(result["cta"], tone)

    return result
