"""Client-side re-ranking of resolve results using local context."""
from __future__ import annotations

from agentnode_sdk.capability_graph import neighbors
from agentnode_sdk.models import ResolvedPackage


def rerank(
    results: list[ResolvedPackage],
    installed_caps: set[str],
    installed_slugs: set[str],
) -> list[ResolvedPackage]:
    """Re-rank resolve results using local context.

    Boosts packages that complement installed capabilities.
    Penalizes packages that overlap with what's already installed.
    """
    scored = []
    for pkg in results:
        boost = 0.0

        for cap in pkg.matched_capabilities:
            for edge in neighbors(cap):
                if edge.target in installed_caps:
                    boost += edge.weight * 5.0

        overlap = len(set(pkg.matched_capabilities) & installed_caps)
        if overlap > 0:
            boost -= overlap * 3.0

        if pkg.slug in installed_slugs:
            boost -= 100.0

        boost = max(min(boost, 10.0), -20.0) if pkg.slug not in installed_slugs else boost

        scored.append((pkg.score + boost, pkg))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [pkg for _, pkg in scored]
