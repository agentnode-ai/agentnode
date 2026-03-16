"""Generate user stories and acceptance criteria from feature descriptions."""

from __future__ import annotations

import re
import hashlib


def run(feature_description: str, format: str = "agile") -> dict:
    """Generate user stories from a feature description.

    Args:
        feature_description: Plain-text description of the feature to plan.
        format: Output format - "agile" (standard user stories) or "technical".

    Returns:
        dict with keys: stories, epic.
    """
    if not feature_description.strip():
        return {"stories": [], "epic": ""}

    # Derive the epic title from the description
    epic = _derive_epic(feature_description)

    # Break the description into actionable components
    components = _decompose_feature(feature_description)

    # Generate stories for each component
    stories: list[dict] = []
    for component in components:
        story = _generate_story(component, format)
        stories.append(story)

    # If we only extracted one generic component, add supporting stories
    if len(stories) == 1:
        stories.extend(_generate_supporting_stories(feature_description, format))

    return {
        "stories": stories,
        "epic": epic,
    }


# ---------------------------------------------------------------------------
# Decomposition helpers
# ---------------------------------------------------------------------------

def _derive_epic(description: str) -> str:
    """Derive a concise epic title from the description."""
    # Take the first sentence or first 80 chars
    first_sentence = description.split(".")[0].strip()
    if len(first_sentence) > 80:
        first_sentence = first_sentence[:77] + "..."
    return first_sentence


def _decompose_feature(description: str) -> list[dict]:
    """Break a feature description into actionable components."""
    components: list[dict] = []

    # Look for explicit bullets or numbered items
    bullet_pattern = re.compile(r"(?:^|\n)\s*(?:[-*]|\d+[.)]) ?\s*(.+)", re.MULTILINE)
    bullets = bullet_pattern.findall(description)

    if bullets:
        for bullet in bullets:
            bullet = bullet.strip()
            if len(bullet) > 5:  # skip very short fragments
                components.append({
                    "text": bullet,
                    "type": _classify_component(bullet),
                })
    else:
        # Split by sentences and cluster by topic
        sentences = _split_sentences(description)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 10:
                components.append({
                    "text": sentence,
                    "type": _classify_component(sentence),
                })

    if not components:
        components.append({
            "text": description.strip(),
            "type": "feature",
        })

    return components


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    # Simple sentence splitter
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _classify_component(text: str) -> str:
    """Classify a component into a category."""
    text_lower = text.lower()

    if any(kw in text_lower for kw in ("login", "auth", "sign in", "register", "permission", "role")):
        return "authentication"
    if any(kw in text_lower for kw in ("ui", "interface", "display", "show", "view", "page", "button", "form")):
        return "ui"
    if any(kw in text_lower for kw in ("api", "endpoint", "rest", "graphql", "request", "response")):
        return "api"
    if any(kw in text_lower for kw in ("database", "store", "save", "persist", "query", "table", "model")):
        return "data"
    if any(kw in text_lower for kw in ("test", "validate", "check", "verify")):
        return "testing"
    if any(kw in text_lower for kw in ("deploy", "ci", "cd", "pipeline", "docker", "kubernetes")):
        return "devops"
    if any(kw in text_lower for kw in ("notify", "email", "sms", "alert", "message")):
        return "notification"
    if any(kw in text_lower for kw in ("search", "filter", "sort", "find")):
        return "search"
    if any(kw in text_lower for kw in ("report", "export", "analytics", "dashboard", "chart")):
        return "reporting"

    return "feature"


# ---------------------------------------------------------------------------
# Story generation
# ---------------------------------------------------------------------------

_ROLE_MAP = {
    "authentication": "user",
    "ui": "user",
    "api": "developer",
    "data": "system administrator",
    "testing": "developer",
    "devops": "DevOps engineer",
    "notification": "user",
    "search": "user",
    "reporting": "business analyst",
    "feature": "user",
}

_BENEFIT_MAP = {
    "authentication": "I can securely access the system",
    "ui": "I have an intuitive and efficient experience",
    "api": "I can integrate with the system programmatically",
    "data": "data is reliably stored and retrievable",
    "testing": "code quality is maintained",
    "devops": "deployments are automated and reliable",
    "notification": "I stay informed about important events",
    "search": "I can quickly find what I need",
    "reporting": "I can make data-driven decisions",
    "feature": "my needs are met efficiently",
}


def _generate_story(component: dict, format: str) -> dict:
    """Generate a single user story from a component."""
    text = component["text"]
    comp_type = component["type"]
    role = _ROLE_MAP.get(comp_type, "user")
    benefit = _BENEFIT_MAP.get(comp_type, "my needs are met")

    # Clean up the action text
    action = _normalize_action(text)

    # Build acceptance criteria
    acceptance_criteria = _generate_criteria(text, comp_type)

    # Generate a story title
    title = _generate_title(text)

    story = {
        "title": title,
        "as_a": role,
        "i_want": action,
        "so_that": benefit,
        "acceptance_criteria": acceptance_criteria,
    }

    if format == "technical":
        story["technical_notes"] = _generate_technical_notes(text, comp_type)
        story["estimated_complexity"] = _estimate_complexity(text)

    return story


def _normalize_action(text: str) -> str:
    """Normalize the action text for user story format."""
    text = text.strip().rstrip(".")
    # Remove leading "I want to", "Users should", etc.
    text = re.sub(r"^(?:I want to|users? (?:should|can|must)|the system (?:should|must|will))\s+", "", text, flags=re.IGNORECASE)
    # Ensure it starts with a verb phrase
    if text and text[0].isupper():
        text = text[0].lower() + text[1:]
    return text


def _generate_title(text: str) -> str:
    """Generate a short story title."""
    # Take the first ~50 chars and capitalize
    clean = text.strip().rstrip(".")
    if len(clean) > 60:
        clean = clean[:57] + "..."
    return clean.capitalize()


def _generate_criteria(text: str, comp_type: str) -> list[str]:
    """Generate acceptance criteria based on component type and text."""
    criteria: list[str] = []

    # Universal criteria
    criteria.append(f"Given the feature is implemented, the described behavior works as specified")

    # Type-specific criteria
    type_criteria = {
        "authentication": [
            "User credentials are validated securely",
            "Appropriate error messages are shown for invalid input",
            "Session is managed correctly (creation, expiry, logout)",
        ],
        "ui": [
            "The interface is responsive across common screen sizes",
            "User interactions provide immediate visual feedback",
            "Accessibility requirements (WCAG 2.1 AA) are met",
        ],
        "api": [
            "Endpoint returns correct HTTP status codes",
            "Request validation rejects malformed input with clear errors",
            "Response format matches the documented schema",
        ],
        "data": [
            "Data is persisted correctly after the operation",
            "Invalid data is rejected with appropriate error messages",
            "Concurrent access does not cause data corruption",
        ],
        "testing": [
            "Test coverage meets the project minimum threshold",
            "Both happy path and error cases are covered",
            "Tests run successfully in CI/CD pipeline",
        ],
        "devops": [
            "Pipeline completes without manual intervention",
            "Rollback mechanism is in place and tested",
            "Deployment logs are accessible for debugging",
        ],
        "notification": [
            "Notifications are delivered within the expected timeframe",
            "Users can configure their notification preferences",
            "Notification content is accurate and actionable",
        ],
        "search": [
            "Search results are relevant and ordered appropriately",
            "Empty or no-match queries display a helpful message",
            "Search completes within acceptable performance limits",
        ],
        "reporting": [
            "Reports display accurate and up-to-date data",
            "Export format is compatible with common tools (Excel, CSV)",
            "Large datasets do not cause timeouts or memory issues",
        ],
        "feature": [
            "The feature works end-to-end without errors",
            "Edge cases are handled gracefully",
        ],
    }

    criteria.extend(type_criteria.get(comp_type, type_criteria["feature"]))

    return criteria


def _generate_technical_notes(text: str, comp_type: str) -> list[str]:
    """Generate technical notes for a story."""
    notes: list[str] = []

    type_notes = {
        "authentication": ["Consider OAuth2 / JWT for token management", "Implement rate limiting on auth endpoints"],
        "ui": ["Use component library for consistency", "Implement loading states and skeleton screens"],
        "api": ["Document with OpenAPI/Swagger", "Implement pagination for list endpoints"],
        "data": ["Add database migration scripts", "Consider indexing for query performance"],
        "testing": ["Set up test fixtures and factories", "Use mocking for external dependencies"],
        "devops": ["Use infrastructure-as-code (Terraform/Pulumi)", "Set up health checks and monitoring"],
        "notification": ["Use a message queue for async delivery", "Implement retry logic with exponential backoff"],
        "search": ["Consider full-text search index (Elasticsearch/Meilisearch)", "Implement debouncing on client side"],
        "reporting": ["Use background jobs for large report generation", "Implement caching for frequently accessed reports"],
        "feature": ["Break into smaller tasks during sprint planning", "Review dependencies with team"],
    }

    notes.extend(type_notes.get(comp_type, type_notes["feature"]))
    return notes


def _estimate_complexity(text: str) -> str:
    """Estimate story complexity as T-shirt size."""
    word_count = len(text.split())
    complexity_keywords = {"integration", "migration", "security", "performance", "scale", "complex", "multiple", "distributed"}
    hits = sum(1 for kw in complexity_keywords if kw in text.lower())

    if hits >= 3 or word_count > 50:
        return "XL"
    elif hits >= 2 or word_count > 30:
        return "L"
    elif hits >= 1 or word_count > 15:
        return "M"
    else:
        return "S"


def _generate_supporting_stories(description: str, format: str) -> list[dict]:
    """Generate supporting stories (testing, documentation) for a single-story epic."""
    stories: list[dict] = []

    # Testing story
    stories.append(_generate_story({
        "text": f"write automated tests for: {description[:100]}",
        "type": "testing",
    }, format))

    # Documentation story
    stories.append({
        "title": "Document the feature and update user guides",
        "as_a": "developer",
        "i_want": "comprehensive documentation for this feature",
        "so_that": "future developers and users can understand the system",
        "acceptance_criteria": [
            "README or relevant docs are updated",
            "API documentation is generated and accurate",
            "Change log entry is added",
        ],
    })

    return stories
