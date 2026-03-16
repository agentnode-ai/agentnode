"""Contract review tool for analyzing legal documents."""

from __future__ import annotations

import re


# Patterns that indicate potentially risky clauses
RISK_PATTERNS = [
    {
        "name": "Non-compete clause",
        "pattern": r"(?i)\bnon[- ]?compet(?:e|ition)\b",
        "severity": "high",
        "advice": "Non-compete clauses can restrict future employment. Check duration, geographic scope, and enforceability in your jurisdiction.",
    },
    {
        "name": "Unlimited liability",
        "pattern": r"(?i)\bunlimited\s+liability\b",
        "severity": "high",
        "advice": "Unlimited liability exposes you to uncapped financial risk. Negotiate a liability cap.",
    },
    {
        "name": "Indemnification",
        "pattern": r"(?i)\bindemnif(?:y|ication|ied)\b",
        "severity": "medium",
        "advice": "Review scope of indemnification carefully. Ensure it is mutual and capped.",
    },
    {
        "name": "Automatic renewal",
        "pattern": r"(?i)\b(?:auto(?:matic(?:ally)?)?[- ]?renew(?:al|s|ed)?)\b",
        "severity": "medium",
        "advice": "Automatic renewal may lock you in. Check notice period for cancellation.",
    },
    {
        "name": "Termination for convenience",
        "pattern": r"(?i)\btermina(?:te|tion)\s+(?:for\s+)?convenience\b",
        "severity": "medium",
        "advice": "One-sided termination for convenience may leave you without recourse. Ensure it is mutual.",
    },
    {
        "name": "Intellectual property assignment",
        "pattern": r"(?i)\b(?:assign(?:s|ment)?|transfer(?:s)?)\s+(?:all\s+)?(?:intellectual\s+property|IP|copyright|patent)\b",
        "severity": "high",
        "advice": "IP assignment clauses transfer ownership of your work. Ensure scope is limited to deliverables.",
    },
    {
        "name": "Confidentiality / NDA",
        "pattern": r"(?i)\b(?:confidential(?:ity)?|non[- ]?disclosure|NDA)\b",
        "severity": "low",
        "advice": "Confidentiality clauses are standard. Check duration and scope of what is considered confidential.",
    },
    {
        "name": "Governing law",
        "pattern": r"(?i)\bgoverning\s+law\b",
        "severity": "low",
        "advice": "Check which jurisdiction's law governs the contract and whether disputes require arbitration.",
    },
    {
        "name": "Arbitration clause",
        "pattern": r"(?i)\barbitrat(?:ion|e|or)\b",
        "severity": "medium",
        "advice": "Arbitration may limit your legal options. Check if it is binding and who selects the arbitrator.",
    },
    {
        "name": "Penalty clause",
        "pattern": r"(?i)\b(?:penalty|liquidated\s+damages)\b",
        "severity": "high",
        "advice": "Penalty clauses can impose significant financial burden. Ensure amounts are reasonable and proportionate.",
    },
    {
        "name": "Force majeure",
        "pattern": r"(?i)\bforce\s+majeure\b",
        "severity": "low",
        "advice": "Force majeure clauses are standard. Verify the list of qualifying events is comprehensive.",
    },
    {
        "name": "Exclusivity",
        "pattern": r"(?i)\bexclusiv(?:e|ity)\b",
        "severity": "high",
        "advice": "Exclusivity clauses prevent working with competitors. Check duration and scope.",
    },
    {
        "name": "Warranty disclaimer",
        "pattern": r"(?i)\b(?:as[- ]is|without\s+warranty|disclaimer\s+of\s+warrant(?:y|ies))\b",
        "severity": "medium",
        "advice": "Warranty disclaimers limit recourse for defects. Consider negotiating minimum warranties.",
    },
    {
        "name": "Data processing / GDPR",
        "pattern": r"(?i)\b(?:data\s+process(?:ing|or)|GDPR|personal\s+data|data\s+protection)\b",
        "severity": "medium",
        "advice": "Ensure data processing terms comply with applicable privacy regulations.",
    },
    {
        "name": "Assignment restriction",
        "pattern": r"(?i)\bmay\s+not\s+(?:be\s+)?assign(?:ed)?\b",
        "severity": "low",
        "advice": "Assignment restrictions prevent transferring the contract. Standard but worth noting.",
    },
]

# Key terms to extract
KEY_TERMS = [
    {"name": "Payment terms", "pattern": r"(?i)\b(?:payment|invoice|billing)\s+(?:terms?|within|net)\b"},
    {"name": "Term / Duration", "pattern": r"(?i)\b(?:term|duration|period)\s+(?:of|shall\s+be)\s+(\d+\s+(?:month|year|day)s?)"},
    {"name": "Notice period", "pattern": r"(?i)\b(\d+)\s+(?:day|business\s+day|month)s?\s+(?:prior\s+)?(?:written\s+)?notice\b"},
    {"name": "Liability cap", "pattern": r"(?i)\bliability\b.{0,50}\b(?:not\s+exceed|cap(?:ped)?|limit(?:ed)?|maximum)\b"},
]


def run(
    text: str,
    check_risks: bool = True,
    extract_terms: bool = True,
    severity_filter: str = "",
) -> dict:
    """Analyze a legal contract text for risky clauses and key terms.

    Args:
        text: The full contract text to analyze.
        check_risks: Whether to scan for risky clause patterns.
        extract_terms: Whether to extract key contract terms.
        severity_filter: Only return risks of this severity ("high", "medium", "low") or "" for all.

    Returns:
        dict with keys: risks, key_terms, summary, stats.
    """
    if not text or not text.strip():
        return {"error": "No contract text provided."}

    results: dict = {
        "risks": [],
        "key_terms": [],
        "summary": {},
        "stats": {
            "word_count": len(text.split()),
            "paragraph_count": len([p for p in text.split("\n\n") if p.strip()]),
        },
    }

    # --- Risk analysis ---
    if check_risks:
        for rule in RISK_PATTERNS:
            matches = list(re.finditer(rule["pattern"], text))
            if matches:
                if severity_filter and rule["severity"] != severity_filter:
                    continue

                locations = []
                for m in matches:
                    start = max(0, m.start() - 80)
                    end = min(len(text), m.end() + 80)
                    context = text[start:end].replace("\n", " ").strip()
                    locations.append({
                        "position": m.start(),
                        "context": f"...{context}...",
                    })

                results["risks"].append({
                    "name": rule["name"],
                    "severity": rule["severity"],
                    "occurrences": len(matches),
                    "advice": rule["advice"],
                    "locations": locations[:3],
                })

    # --- Key terms extraction ---
    if extract_terms:
        for term in KEY_TERMS:
            matches = list(re.finditer(term["pattern"], text))
            if matches:
                extracts = []
                for m in matches:
                    start = max(0, m.start() - 40)
                    end = min(len(text), m.end() + 80)
                    snippet = text[start:end].replace("\n", " ").strip()
                    extracts.append(f"...{snippet}...")

                results["key_terms"].append({
                    "name": term["name"],
                    "found": True,
                    "occurrences": len(matches),
                    "extracts": extracts[:3],
                })

    # --- Summary ---
    high_risks = sum(1 for r in results["risks"] if r["severity"] == "high")
    medium_risks = sum(1 for r in results["risks"] if r["severity"] == "medium")
    low_risks = sum(1 for r in results["risks"] if r["severity"] == "low")

    if high_risks >= 3:
        overall = "high_risk"
    elif high_risks >= 1 or medium_risks >= 3:
        overall = "moderate_risk"
    elif medium_risks >= 1:
        overall = "low_risk"
    else:
        overall = "minimal_risk"

    results["summary"] = {
        "overall_risk": overall,
        "high_risk_count": high_risks,
        "medium_risk_count": medium_risks,
        "low_risk_count": low_risks,
        "total_risks_found": len(results["risks"]),
        "key_terms_found": len(results["key_terms"]),
    }

    return results
