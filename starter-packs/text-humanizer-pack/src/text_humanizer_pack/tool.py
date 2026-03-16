"""Text humanizer tool using regex-based transformations."""

from __future__ import annotations

import random
import re


# Contraction mappings
_CONTRACTIONS = {
    r"\bI am\b": "I'm",
    r"\bI have\b": "I've",
    r"\bI will\b": "I'll",
    r"\bI would\b": "I'd",
    r"\byou are\b": "you're",
    r"\byou have\b": "you've",
    r"\byou will\b": "you'll",
    r"\byou would\b": "you'd",
    r"\bhe is\b": "he's",
    r"\bhe has\b": "he's",
    r"\bhe will\b": "he'll",
    r"\bhe would\b": "he'd",
    r"\bshe is\b": "she's",
    r"\bshe has\b": "she's",
    r"\bshe will\b": "she'll",
    r"\bshe would\b": "she'd",
    r"\bit is\b": "it's",
    r"\bit has\b": "it's",
    r"\bit will\b": "it'll",
    r"\bwe are\b": "we're",
    r"\bwe have\b": "we've",
    r"\bwe will\b": "we'll",
    r"\bwe would\b": "we'd",
    r"\bthey are\b": "they're",
    r"\bthey have\b": "they've",
    r"\bthey will\b": "they'll",
    r"\bthey would\b": "they'd",
    r"\bthat is\b": "that's",
    r"\bthere is\b": "there's",
    r"\bwho is\b": "who's",
    r"\bwho has\b": "who's",
    r"\bwhat is\b": "what's",
    r"\bwhat has\b": "what's",
    r"\bwhere is\b": "where's",
    r"\bwhen is\b": "when's",
    r"\bwhy is\b": "why's",
    r"\bhow is\b": "how's",
    r"\bdo not\b": "don't",
    r"\bdoes not\b": "doesn't",
    r"\bdid not\b": "didn't",
    r"\bwill not\b": "won't",
    r"\bwould not\b": "wouldn't",
    r"\bcould not\b": "couldn't",
    r"\bshould not\b": "shouldn't",
    r"\bcan not\b": "can't",
    r"\bcannot\b": "can't",
    r"\bis not\b": "isn't",
    r"\bare not\b": "aren't",
    r"\bwas not\b": "wasn't",
    r"\bwere not\b": "weren't",
    r"\bhas not\b": "hasn't",
    r"\bhave not\b": "haven't",
    r"\bhad not\b": "hadn't",
    r"\blet us\b": "let's",
}

# Corporate jargon replacements
_JARGON = {
    r"\bleverage\b": "use",
    r"\bLeverage\b": "Use",
    r"\butilize\b": "use",
    r"\bUtilize\b": "Use",
    r"\butilization\b": "use",
    r"\bfacilitate\b": "help",
    r"\bFacilitate\b": "Help",
    r"\bsynergize\b": "work together",
    r"\bSynergize\b": "Work together",
    r"\bsynergy\b": "teamwork",
    r"\bparadigm shift\b": "big change",
    r"\bparadigm\b": "approach",
    r"\boptimize\b": "improve",
    r"\bOptimize\b": "Improve",
    r"\bstreamline\b": "simplify",
    r"\bStreamline\b": "Simplify",
    r"\bimpactful\b": "effective",
    r"\bactionable\b": "practical",
    r"\bActionable\b": "Practical",
    r"\brobust\b": "strong",
    r"\bRobust\b": "Strong",
    r"\bscalable\b": "flexible",
    r"\bScalable\b": "Flexible",
    r"\binnovative solution\b": "new idea",
    r"\bInnovative solution\b": "New idea",
    r"\bvalue proposition\b": "benefit",
    r"\bValue proposition\b": "Benefit",
    r"\bcore competency\b": "strength",
    r"\bCore competency\b": "Strength",
    r"\bcore competencies\b": "strengths",
    r"\bstakeholders\b": "people involved",
    r"\bStakeholders\b": "People involved",
    r"\bholistic approach\b": "complete view",
    r"\bHolistic approach\b": "Complete view",
    r"\bmoving forward\b": "from now on",
    r"\bMoving forward\b": "From now on",
    r"\bgoing forward\b": "from now on",
    r"\bGoing forward\b": "From now on",
    r"\bat the end of the day\b": "ultimately",
    r"\bAt the end of the day\b": "Ultimately",
    r"\bin order to\b": "to",
    r"\bIn order to\b": "To",
    r"\bdue to the fact that\b": "because",
    r"\bDue to the fact that\b": "Because",
    r"\bat this point in time\b": "now",
    r"\bAt this point in time\b": "Now",
    r"\bprior to\b": "before",
    r"\bPrior to\b": "Before",
    r"\bsubsequent to\b": "after",
    r"\bSubsequent to\b": "After",
    r"\bin the event that\b": "if",
    r"\bIn the event that\b": "If",
    r"\bwith regard to\b": "about",
    r"\bWith regard to\b": "About",
    r"\bin regards to\b": "about",
    r"\bIn regards to\b": "About",
    r"\bwith respect to\b": "about",
    r"\bWith respect to\b": "About",
    r"\bpertainting to\b": "about",
    r"\bis comprised of\b": "includes",
    r"\bIs comprised of\b": "Includes",
    r"\bin conjunction with\b": "with",
    r"\bIn conjunction with\b": "With",
    r"\bnotwithstanding\b": "despite",
    r"\bNotwithstanding\b": "Despite",
    r"\bhereby\b": "",
    r"\bthereby\b": "so",
    r"\btherein\b": "there",
}

# Transition words/phrases for natural flow
_TRANSITIONS_CASUAL = [
    "Anyway, ",
    "So, ",
    "Plus, ",
    "Also, ",
    "And honestly, ",
    "On top of that, ",
    "Thing is, ",
    "Look, ",
    "Here's the deal: ",
    "Basically, ",
]

_TRANSITIONS_PROFESSIONAL = [
    "That said, ",
    "Additionally, ",
    "Moreover, ",
    "In short, ",
    "To clarify, ",
    "Put simply, ",
    "Worth noting: ",
]

# Filler starters to remove for conciseness
_STIFF_STARTERS = [
    r"^It is important to note that ",
    r"^It should be noted that ",
    r"^It is worth mentioning that ",
    r"^It is essential to ",
    r"^It has come to our attention that ",
    r"^Please be advised that ",
    r"^Please note that ",
    r"^We would like to inform you that ",
    r"^We are pleased to inform you that ",
    r"^This is to inform you that ",
    r"^As per our discussion, ",
    r"^As previously mentioned, ",
    r"^Pursuant to ",
]


def _apply_contractions(text: str) -> str:
    """Replace formal forms with contractions."""
    for pattern, replacement in _CONTRACTIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _remove_jargon(text: str) -> str:
    """Replace corporate jargon with plain language."""
    for pattern, replacement in _JARGON.items():
        text = re.sub(pattern, replacement, text)
    return text


def _remove_stiff_starters(text: str) -> str:
    """Remove unnecessarily formal sentence starters."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    cleaned = []
    for sentence in sentences:
        for starter in _STIFF_STARTERS:
            sentence = re.sub(starter, "", sentence, flags=re.IGNORECASE)
        if sentence:
            # Capitalize the first letter after removing a starter
            sentence = sentence[0].upper() + sentence[1:] if len(sentence) > 1 else sentence.upper()
            cleaned.append(sentence)
    return " ".join(cleaned)


def _vary_sentence_length(text: str, style: str) -> str:
    """Break up long sentences and occasionally combine short ones."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = []

    i = 0
    while i < len(sentences):
        sentence = sentences[i]

        # Break very long sentences (>30 words) at conjunctions
        words = sentence.split()
        if len(words) > 30:
            # Try to split at 'and', 'but', 'however', 'which', 'although'
            split_points = []
            for j, w in enumerate(words):
                if w.lower().rstrip(",") in ("and", "but", "however", "although", "whereas") and j > 8:
                    split_points.append(j)
            if split_points:
                mid = split_points[len(split_points) // 2]
                first_part = " ".join(words[:mid]).rstrip(",") + "."
                second_word = words[mid].lstrip(",")
                if second_word.lower() in ("and", "but"):
                    second_part = " ".join(words[mid + 1:])
                else:
                    second_part = " ".join(words[mid:])
                if second_part:
                    second_part = second_part[0].upper() + second_part[1:]
                result.append(first_part)
                result.append(second_part)
                i += 1
                continue

        # Combine very short consecutive sentences (< 5 words each) in casual style
        if style == "casual" and len(words) < 5 and i + 1 < len(sentences):
            next_words = sentences[i + 1].split()
            if len(next_words) < 5:
                combined = sentence.rstrip(".!?") + " -- " + sentences[i + 1]
                result.append(combined)
                i += 2
                continue

        result.append(sentence)
        i += 1

    return " ".join(result)


def _add_transitions(text: str, style: str) -> str:
    """Add natural transitions between some sentences."""
    rng = random.Random(hash(text) % 2**32)  # Deterministic based on input
    transitions = _TRANSITIONS_CASUAL if style == "casual" else _TRANSITIONS_PROFESSIONAL

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 2:
        return text

    result = [sentences[0]]

    for i, sentence in enumerate(sentences[1:], 1):
        # Add a transition roughly every 3-4 sentences, not on the last one
        if i % 3 == 0 and i < len(sentences) - 1 and not any(sentence.startswith(t.strip()) for t in transitions):
            transition = rng.choice(transitions)
            # Lowercase the first letter of the sentence after transition
            if sentence and sentence[0].isupper():
                sentence = sentence[0].lower() + sentence[1:]
            sentence = transition + sentence

        result.append(sentence)

    return " ".join(result)


def _casual_touches(text: str) -> str:
    """Add casual-style tweaks."""
    # Replace some formal words with casual equivalents
    casual_swaps = {
        r"\bHowever\b": "But",
        r"\bTherefore\b": "So",
        r"\bFurthermore\b": "Plus",
        r"\bConsequently\b": "So",
        r"\bNevertheless\b": "Still",
        r"\bAdditionally\b": "Also",
        r"\bMoreover\b": "On top of that",
        r"\bNonetheless\b": "Even so",
        r"\bsubstantial\b": "big",
        r"\bnumerous\b": "many",
        r"\bcommence\b": "start",
        r"\bCommence\b": "Start",
        r"\bterminate\b": "end",
        r"\bTerminate\b": "End",
        r"\bsufficient\b": "enough",
        r"\bSufficient\b": "Enough",
        r"\bpurchase\b": "buy",
        r"\bPurchase\b": "Buy",
        r"\binquire\b": "ask",
        r"\bInquire\b": "Ask",
        r"\bassist\b": "help",
        r"\bAssist\b": "Help",
        r"\bendeavor\b": "try",
        r"\bEndeavor\b": "Try",
        r"\bindividuals\b": "people",
        r"\bIndividuals\b": "People",
    }
    for pattern, replacement in casual_swaps.items():
        text = re.sub(pattern, replacement, text)

    return text


def run(text: str, style: str = "casual") -> dict:
    """Transform text into more natural, human-sounding language.

    Args:
        text: The input text to humanize.
        style: Transformation style. One of:
            - "casual": Conversational, friendly tone with contractions and simpler words.
            - "professional": Clean and clear but still natural, avoids jargon without being too informal.

    Returns:
        A dict with the original and humanized text.
    """
    if not text or not text.strip():
        return {"original": text, "humanized": text, "style": style, "changes": []}

    valid_styles = ("casual", "professional")
    if style not in valid_styles:
        return {"error": f"Unknown style '{style}'. Use one of {valid_styles}."}

    changes: list[str] = []
    result = text

    # Step 1: Remove stiff sentence starters
    new_result = _remove_stiff_starters(result)
    if new_result != result:
        changes.append("removed_stiff_starters")
    result = new_result

    # Step 2: Replace corporate jargon
    new_result = _remove_jargon(result)
    if new_result != result:
        changes.append("replaced_jargon")
    result = new_result

    # Step 3: Add contractions
    if style == "casual":
        new_result = _apply_contractions(result)
        if new_result != result:
            changes.append("added_contractions")
        result = new_result

    # Step 4: Casual word swaps
    if style == "casual":
        new_result = _casual_touches(result)
        if new_result != result:
            changes.append("casual_word_swaps")
        result = new_result

    # Step 5: Vary sentence length
    new_result = _vary_sentence_length(result, style)
    if new_result != result:
        changes.append("varied_sentence_length")
    result = new_result

    # Step 6: Add natural transitions
    new_result = _add_transitions(result, style)
    if new_result != result:
        changes.append("added_transitions")
    result = new_result

    # Clean up any double spaces
    result = re.sub(r"  +", " ", result).strip()

    return {
        "original": text,
        "humanized": result,
        "style": style,
        "changes": changes,
    }
