"""Regex builder and tester tool using the re module."""

from __future__ import annotations

import re
from typing import Any


def run(
    pattern: str,
    test_string: str = "",
    operation: str = "test",
    **kwargs: Any,
) -> dict:
    """Build, test, and apply regular expressions.

    Args:
        pattern: The regex pattern to use.
        test_string: The string to apply the pattern against.
        operation: One of "test", "findall", "split", "sub".
        **kwargs:
            replacement (str): Replacement string for "sub" operation.
            flags (list[str]): Regex flags like ["IGNORECASE", "MULTILINE"].

    Returns:
        A dict with the operation result.
    """
    # Build flags
    flag_names = kwargs.get("flags", [])
    combined_flags = 0
    flag_map = {
        "IGNORECASE": re.IGNORECASE,
        "I": re.IGNORECASE,
        "MULTILINE": re.MULTILINE,
        "M": re.MULTILINE,
        "DOTALL": re.DOTALL,
        "S": re.DOTALL,
        "VERBOSE": re.VERBOSE,
        "X": re.VERBOSE,
        "ASCII": re.ASCII,
        "A": re.ASCII,
    }
    for flag_name in flag_names:
        flag_val = flag_map.get(flag_name.upper())
        if flag_val is not None:
            combined_flags |= flag_val

    # Compile the pattern
    try:
        compiled = re.compile(pattern, combined_flags)
    except re.error as exc:
        return {
            "error": f"Invalid regex pattern: {exc}",
            "pattern": pattern,
        }

    result: dict[str, Any] = {
        "pattern": pattern,
        "operation": operation,
        "groups_in_pattern": compiled.groups,
        "group_names": list(compiled.groupindex.keys()),
    }

    if not test_string:
        result["note"] = "No test_string provided. Pattern compiled successfully."
        return result

    result["test_string"] = test_string

    if operation == "test":
        match = compiled.search(test_string)
        if match:
            result["match"] = True
            result["matched_text"] = match.group()
            result["span"] = list(match.span())
            result["groups"] = list(match.groups())
            if match.groupdict():
                result["named_groups"] = match.groupdict()

            # Also provide full match for the entire string
            full_match = compiled.fullmatch(test_string)
            result["full_match"] = full_match is not None
        else:
            result["match"] = False
            result["matched_text"] = None

    elif operation == "findall":
        matches = list(compiled.finditer(test_string))
        result["count"] = len(matches)
        result["matches"] = []
        for m in matches:
            match_info: dict[str, Any] = {
                "text": m.group(),
                "span": list(m.span()),
            }
            if m.groups():
                match_info["groups"] = list(m.groups())
            if m.groupdict():
                match_info["named_groups"] = m.groupdict()
            result["matches"].append(match_info)

    elif operation == "split":
        parts = compiled.split(test_string)
        result["parts"] = parts
        result["count"] = len(parts)

    elif operation == "sub":
        replacement = kwargs.get("replacement", "")
        new_string, count = compiled.subn(replacement, test_string)
        result["result"] = new_string
        result["replacement"] = replacement
        result["substitutions_made"] = count

    else:
        result["error"] = f"Unknown operation '{operation}'. Use test, findall, split, or sub."

    return result
