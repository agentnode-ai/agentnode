"""Tests for the import conversion endpoint and service."""
from __future__ import annotations

import ast
import pytest

from app.import_.schemas import ConvertRequest
from app.import_.service import convert


# ── Fixtures ─────────────────────────────────────────────────────────

SIMPLE_TOOL = '''
from langchain.tools import tool

@tool
def word_count(text: str) -> dict:
    """Count words in text."""
    words = text.split()
    return {"count": len(words)}
'''

BASETOOL_STR = '''
from langchain.tools import BaseTool

class WebSearch(BaseTool):
    name = "web_search"
    description = "Search the web"
    def _run(self, query: str) -> str:
        import requests
        resp = requests.get(f"https://api.example.com/search?q={query}")
        return resp.text
'''

UNRESOLVED = '''
from langchain.tools import tool

@tool
def analyze(data: str) -> dict:
    """Analyze data."""
    result = custom_analyzer(data)
    return {"analysis": result}
'''

ASYNC_TOOL = '''
from langchain.tools import tool

@tool
async def fetch_url(url: str) -> dict:
    """Fetch URL content."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return {"content": await resp.text()}
'''

NO_PATTERN = '''
def helper_function():
    return 42
'''

CREWAI_NAMED = '''
from crewai_tools import tool

@tool("Summarize Document")
def summarize_document(file_path: str, max_length: int = 500) -> dict:
    """Read a document and return a concise summary."""
    with open(file_path) as f:
        content = f.read()
    return {"summary": content[:max_length]}
'''

UNKNOWN_IMPORT_ACTIVE = '''
from langchain.tools import tool
from my_internal_lib import helper

@tool
def process(data: str) -> dict:
    """Process data."""
    return {"result": helper(data)}
'''

SELF_REFERENCE = '''
from langchain.tools import BaseTool

class ApiTool(BaseTool):
    name = "api_call"
    description = "Call an API"
    api_key: str = ""
    def _run(self, endpoint: str) -> dict:
        import requests
        resp = requests.get(endpoint, headers={"Authorization": self.api_key})
        return {"data": resp.json()}
'''

UNKNOWN_IMPORT_UNUSED = '''
from langchain.tools import tool
from my_internal_lib import helper
import pandas

@tool
def count(text: str) -> dict:
    """Count words."""
    return {"count": len(text.split())}
'''


# ── Tests ────────────────────────────────────────────────────────────

class TestSimpleTool:
    """Fixture 1: Simple @tool -> high confidence, draft_ready=True"""

    def test_basic_conversion(self):
        req = ConvertRequest(platform="langchain", content=SIMPLE_TOOL)
        resp = convert(req)

        assert resp.confidence.level == "high"
        assert resp.draft_ready is True
        assert resp.requires_manual_review is False
        assert len(resp.code_files) == 5
        assert len(resp.detected_tools) == 1
        assert resp.detected_tools[0].name == "word_count"
        assert resp.package_id == "word-count-pack"

    def test_generated_code_is_valid(self):
        req = ConvertRequest(platform="langchain", content=SIMPLE_TOOL)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        ast.parse(tool_py.content)  # Must not raise

    def test_no_framework_imports(self):
        req = ConvertRequest(platform="langchain", content=SIMPLE_TOOL)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        assert "langchain" not in tool_py.content

    def test_manifest_is_valid(self):
        req = ConvertRequest(platform="langchain", content=SIMPLE_TOOL)
        resp = convert(req)

        assert resp.manifest_json["manifest_version"] in ("0.1", "0.2")
        assert resp.manifest_json["package_id"] == "word-count-pack"
        assert resp.manifest_json["runtime"] == "python"
        assert len(resp.manifest_json["capabilities"]["tools"]) == 1

    def test_changes_list_non_empty(self):
        req = ConvertRequest(platform="langchain", content=SIMPLE_TOOL)
        resp = convert(req)

        assert len(resp.changes) >= 1


class TestBaseToolStr:
    """Fixture 2: BaseTool with str return -> medium, requires_manual_review"""

    def test_confidence_medium(self):
        req = ConvertRequest(platform="langchain", content=BASETOOL_STR)
        resp = convert(req)

        assert resp.confidence.level == "medium"
        assert resp.requires_manual_review is True
        assert resp.draft_ready is True

    def test_str_return_wrapped(self):
        req = ConvertRequest(platform="langchain", content=BASETOOL_STR)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        assert '{"result":' in tool_py.content or "{'result':" in tool_py.content or '"result"' in tool_py.content

    def test_requests_in_dependencies(self):
        req = ConvertRequest(platform="langchain", content=BASETOOL_STR)
        resp = convert(req)

        assert "requests" in resp.detected_dependencies


class TestUnresolved:
    """Fixture 3: Unresolved symbols -> medium, requires_manual_review"""

    def test_confidence_medium(self):
        req = ConvertRequest(platform="langchain", content=UNRESOLVED)
        resp = convert(req)

        assert resp.confidence.level == "medium"
        assert resp.requires_manual_review is True

    def test_has_unresolved_warning(self):
        req = ConvertRequest(platform="langchain", content=UNRESOLVED)
        resp = convert(req)

        assert any("custom_analyzer" in w for w in resp.warnings)


class TestAsyncTool:
    """Fixture 4: async -> LOW, draft_ready=FALSE"""

    def test_confidence_low(self):
        req = ConvertRequest(platform="langchain", content=ASYNC_TOOL)
        resp = convert(req)

        assert resp.confidence.level == "low"
        assert resp.draft_ready is False

    def test_has_async_warning(self):
        req = ConvertRequest(platform="langchain", content=ASYNC_TOOL)
        resp = convert(req)

        assert any("async" in w.lower() for w in resp.warnings)


class TestNoPattern:
    """Fixture 5: No pattern -> low, draft_ready=False"""

    def test_confidence_low(self):
        req = ConvertRequest(platform="langchain", content=NO_PATTERN)
        resp = convert(req)

        assert resp.confidence.level == "low"
        assert resp.draft_ready is False

    def test_no_tools_detected(self):
        req = ConvertRequest(platform="langchain", content=NO_PATTERN)
        resp = convert(req)

        assert len(resp.detected_tools) == 0


class TestCrewAINamed:
    """Fixture 6: CrewAI @tool with decorator name"""

    def test_high_confidence(self):
        req = ConvertRequest(platform="crewai", content=CREWAI_NAMED)
        resp = convert(req)

        assert resp.confidence.level == "high"
        assert resp.draft_ready is True
        assert len(resp.code_files) == 5

    def test_tool_name_from_decorator(self):
        req = ConvertRequest(platform="crewai", content=CREWAI_NAMED)
        resp = convert(req)

        assert resp.detected_tools[0].original_name == "Summarize Document"
        assert resp.detected_tools[0].name == "summarize_document"

    def test_generated_code_is_valid(self):
        req = ConvertRequest(platform="crewai", content=CREWAI_NAMED)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        ast.parse(tool_py.content)

    def test_no_framework_imports(self):
        req = ConvertRequest(platform="crewai", content=CREWAI_NAMED)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        assert "crewai" not in tool_py.content


class TestUnknownImportActive:
    """Fixture 7: Unknown import active in body -> LOW, draft_ready=FALSE"""

    def test_confidence_low(self):
        req = ConvertRequest(platform="langchain", content=UNKNOWN_IMPORT_ACTIVE)
        resp = convert(req)

        assert resp.confidence.level == "low"
        assert resp.draft_ready is False

    def test_unknown_import_in_list(self):
        req = ConvertRequest(platform="langchain", content=UNKNOWN_IMPORT_ACTIVE)
        resp = convert(req)

        assert "my_internal_lib" in resp.unknown_imports

    def test_not_in_dependencies(self):
        req = ConvertRequest(platform="langchain", content=UNKNOWN_IMPORT_ACTIVE)
        resp = convert(req)

        assert "my_internal_lib" not in resp.detected_dependencies


class TestSelfReference:
    """Fixture 8: self-reference in BaseTool -> LOW, draft_ready=FALSE"""

    def test_confidence_low(self):
        req = ConvertRequest(platform="langchain", content=SELF_REFERENCE)
        resp = convert(req)

        assert resp.confidence.level == "low"
        assert resp.draft_ready is False

    def test_has_self_warning(self):
        req = ConvertRequest(platform="langchain", content=SELF_REFERENCE)
        resp = convert(req)

        assert any("self.api_key" in w for w in resp.warnings)


class TestUnknownImportUnused:
    """Fixture 9: Unknown import not used in body -> MEDIUM (warning only)"""

    def test_confidence_medium(self):
        req = ConvertRequest(platform="langchain", content=UNKNOWN_IMPORT_UNUSED)
        resp = convert(req)

        assert resp.confidence.level == "medium"
        assert resp.draft_ready is True

    def test_unknown_in_warnings(self):
        req = ConvertRequest(platform="langchain", content=UNKNOWN_IMPORT_UNUSED)
        resp = convert(req)

        assert any("my_internal_lib" in w for w in resp.warnings)


class TestEntrypoints:
    """Verify entrypoint format based on tool count."""

    def test_single_tool_v01(self):
        req = ConvertRequest(platform="langchain", content=SIMPLE_TOOL)
        resp = convert(req)

        version = resp.manifest_json.get("manifest_version")
        # Single tool -> v0.1 or v0.2 with correct entrypoint
        assert version in ("0.1", "0.2")
        assert resp.manifest_json["entrypoint"].endswith(".tool")

    def test_manifest_has_entrypoint(self):
        req = ConvertRequest(platform="crewai", content=CREWAI_NAMED)
        resp = convert(req)

        tools = resp.manifest_json["capabilities"]["tools"]
        assert len(tools) >= 1


class TestOnlyConfirmedDepsInPyproject:
    """Only confirmed third-party deps in pyproject.toml, not unknown imports."""

    def test_unknown_not_in_pyproject(self):
        req = ConvertRequest(platform="langchain", content=UNKNOWN_IMPORT_ACTIVE)
        resp = convert(req)

        pyproject = next((f for f in resp.code_files if f.path == "pyproject.toml"), None)
        if pyproject:
            assert "my_internal_lib" not in pyproject.content


# ── Real-world killer cases ──────────────────────────────────────────

ENV_VAR_TOOL = '''
from langchain.tools import tool
import os

API_KEY = os.getenv("API_KEY")

@tool
def search(query: str) -> dict:
    """Search with API."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    return {"result": query}
'''

TRY_EXCEPT_IMPORT = '''
from langchain.tools import tool

@tool
def safe_fetch(url: str) -> dict:
    """Fetch safely."""
    try:
        import requests
        return {"ok": requests.get(url).ok}
    except Exception:
        return {"ok": False}
'''

STRUCTURED_TOOL = '''
from langchain.tools import StructuredTool

def do_search(query: str) -> dict:
    return {"result": query}

search_tool = StructuredTool.from_function(
    func=do_search,
    name="search",
    description="Search"
)
'''

NESTED_IMPORT = '''
from langchain.tools import tool

@tool
def fetch(url: str) -> dict:
    """Fetch a URL."""
    import requests
    return {"status": requests.get(url).status_code}
'''

SHARED_HELPER = '''
from langchain.tools import tool

def _normalize(text):
    return text.lower().strip()

@tool
def analyze(text: str) -> dict:
    """Analyze text."""
    clean = _normalize(text)
    return {"length": len(clean)}

@tool
def count(text: str) -> dict:
    """Count words."""
    clean = _normalize(text)
    return {"count": len(clean.split())}
'''


class TestEnvVarDetection:
    """Killer case 1: os.getenv() usage should warn about runtime context."""

    def test_env_var_warning(self):
        req = ConvertRequest(platform="langchain", content=ENV_VAR_TOOL)
        resp = convert(req)

        assert any("API_KEY" in w and "environment" in w.lower() for w in resp.warnings)

    def test_code_still_includes_global(self):
        req = ConvertRequest(platform="langchain", content=ENV_VAR_TOOL)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        assert "API_KEY" in tool_py.content
        assert "os.getenv" in tool_py.content


class TestTryExceptImport:
    """Killer case 4: Import inside try/except should warn about optional dependency."""

    def test_optional_dep_warning(self):
        req = ConvertRequest(platform="langchain", content=TRY_EXCEPT_IMPORT)
        resp = convert(req)

        assert any("requests" in w and "try/except" in w for w in resp.warnings)

    def test_dep_still_detected(self):
        req = ConvertRequest(platform="langchain", content=TRY_EXCEPT_IMPORT)
        resp = convert(req)

        assert "requests" in resp.detected_dependencies


class TestStructuredToolDetection:
    """Killer case 3: StructuredTool.from_function() should be flagged."""

    def test_structured_tool_warning(self):
        req = ConvertRequest(platform="langchain", content=STRUCTURED_TOOL)
        resp = convert(req)

        assert any("StructuredTool.from_function" in w for w in resp.warnings)


class TestNestedImport:
    """Killer case 2: Import inside function body should still be detected."""

    def test_nested_import_in_deps(self):
        req = ConvertRequest(platform="langchain", content=NESTED_IMPORT)
        resp = convert(req)

        assert "requests" in resp.detected_dependencies

    def test_nested_import_in_pyproject(self):
        req = ConvertRequest(platform="langchain", content=NESTED_IMPORT)
        resp = convert(req)

        pyproject = next(f for f in resp.code_files if f.path == "pyproject.toml")
        assert "requests" in pyproject.content


class TestSharedHelpers:
    """Killer case 5: Multiple tools sharing a helper function."""

    def test_both_tools_extracted(self):
        req = ConvertRequest(platform="langchain", content=SHARED_HELPER)
        resp = convert(req)

        names = {t.name for t in resp.detected_tools}
        assert "analyze" in names
        assert "count" in names

    def test_helper_included_in_output(self):
        req = ConvertRequest(platform="langchain", content=SHARED_HELPER)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        assert "_normalize" in tool_py.content

    def test_generated_code_valid(self):
        req = ConvertRequest(platform="langchain", content=SHARED_HELPER)
        resp = convert(req)

        tool_py = next(f for f in resp.code_files if f.path.endswith("tool.py"))
        ast.parse(tool_py.content)

    def test_no_unresolved_for_helper(self):
        req = ConvertRequest(platform="langchain", content=SHARED_HELPER)
        resp = convert(req)

        assert not any("_normalize" in w and "not defined" in w for w in resp.warnings)


# ── Return Type Classification Tests ──────────────────────────────────

from app.import_.converters.base import ReturnKind, classify_return_annotation


class TestClassifyReturnAnnotation:
    """Unit tests for centralized return type classification."""

    def test_dict(self):
        assert classify_return_annotation("dict") == ReturnKind.DICT

    def test_dict_generic(self):
        assert classify_return_annotation("dict[str, Any]") == ReturnKind.DICT

    def test_Dict_generic(self):
        assert classify_return_annotation("Dict[str, Any]") == ReturnKind.DICT

    def test_str(self):
        assert classify_return_annotation("str") == ReturnKind.STR

    def test_list_bare(self):
        assert classify_return_annotation("list") == ReturnKind.LIST

    def test_List_bare(self):
        assert classify_return_annotation("List") == ReturnKind.LIST

    def test_list_generic(self):
        assert classify_return_annotation("list[str]") == ReturnKind.LIST

    def test_List_generic(self):
        assert classify_return_annotation("List[str]") == ReturnKind.LIST

    def test_tuple(self):
        assert classify_return_annotation("tuple[str, int]") == ReturnKind.TUPLE

    def test_none(self):
        assert classify_return_annotation("None") == ReturnKind.NONE

    def test_optional_dict(self):
        assert classify_return_annotation("Optional[dict]") == ReturnKind.DICT

    def test_union_dict_str(self):
        assert classify_return_annotation("Union[dict, str]") == ReturnKind.UNION

    def test_no_annotation(self):
        assert classify_return_annotation(None) == ReturnKind.UNKNOWN

    def test_int(self):
        assert classify_return_annotation("int") == ReturnKind.UNKNOWN

    def test_any(self):
        assert classify_return_annotation("Any") == ReturnKind.UNKNOWN


class TestReturnTypePolicy:
    """Integration tests: return type → confidence + warnings + wrapping."""

    RETURN_LIST_STR = '''
from langchain.tools import tool

@tool
def get_items(category: str) -> list[str]:
    """Get items by category."""
    return ["item1", "item2"]
'''

    RETURN_LIST_GENERIC = '''
from langchain.tools import BaseTool
from typing import List

class ListMaker(BaseTool):
    name = "list_maker"
    description = "Makes lists"
    def _run(self) -> List[str]:
        """Return a list."""
        return ["a", "b", "c"]
'''

    RETURN_TUPLE = '''
from langchain.tools import tool

@tool
def get_pair(key: str) -> tuple[str, int]:
    """Get a key-value pair."""
    return (key, 42)
'''

    RETURN_UNION = '''
from langchain.tools import tool

@tool
def maybe_fetch(url: str) -> dict | str:
    """Fetch or return error string."""
    return {"ok": True}
'''

    RETURN_NONE = '''
from langchain.tools import tool

@tool
def fire_and_forget(event: str) -> None:
    """Fire an event."""
    pass
'''

    def test_list_str_gets_warning_and_wrap(self):
        resp = convert(ConvertRequest(platform="langchain", content=self.RETURN_LIST_STR))
        assert any("list" in w.lower() and "wrapped" in w.lower() for w in resp.warnings)
        assert resp.confidence.level == "medium"
        assert resp.draft_ready is True

    def test_list_generic_gets_warning_and_wrap(self):
        resp = convert(ConvertRequest(platform="langchain", content=self.RETURN_LIST_GENERIC))
        assert any("list" in w.lower() and "wrapped" in w.lower() for w in resp.warnings)
        assert resp.confidence.level == "medium"
        assert resp.draft_ready is True

    def test_tuple_is_low(self):
        resp = convert(ConvertRequest(platform="langchain", content=self.RETURN_TUPLE))
        assert resp.confidence.level == "low"
        assert resp.draft_ready is False
        assert any("tuple" in w.lower() for w in resp.warnings)

    def test_union_is_low(self):
        resp = convert(ConvertRequest(platform="langchain", content=self.RETURN_UNION))
        assert resp.confidence.level == "low"
        assert resp.draft_ready is False
        assert any("mixed" in w.lower() or "ambiguous" in w.lower() for w in resp.warnings)

    def test_none_is_medium(self):
        resp = convert(ConvertRequest(platform="langchain", content=self.RETURN_NONE))
        assert resp.confidence.level == "medium"
        assert any("none" in w.lower() for w in resp.warnings)

    def test_dict_stays_high(self):
        resp = convert(ConvertRequest(platform="langchain", content=SIMPLE_TOOL))
        assert resp.confidence.level == "high"
        # No return-type warnings
        assert not any("wrapped" in w.lower() for w in resp.warnings)

    def test_non_dict_return_always_has_warning(self):
        """Invariant: non-dict return type must ALWAYS produce a warning."""
        for fixture in [
            self.RETURN_LIST_STR, self.RETURN_LIST_GENERIC,
            self.RETURN_TUPLE, self.RETURN_UNION, self.RETURN_NONE,
        ]:
            resp = convert(ConvertRequest(platform="langchain", content=fixture))
            return_warnings = [
                w for w in resp.warnings
                if "return" in w.lower() or "wrapped" in w.lower() or "anp expects" in w.lower()
            ]
            assert len(return_warnings) > 0, (
                f"Non-dict return fixture produced no return-type warning. "
                f"Warnings: {resp.warnings}"
            )


# ── Sprint C Tag 3: Calibration regression tests ───────────────────


class TestCalibrationRegression:
    """Regression tests for Sprint C calibration changes."""

    def test_langchain_openai_is_known_third_party(self):
        """langchain_openai should be recognized as third-party, not unknown."""
        source = '''
from langchain.tools import tool
from langchain_openai import ChatOpenAI

@tool
def summarize(text: str) -> dict:
    """Summarize text."""
    return {"summary": text[:100]}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        assert "langchain_openai" not in resp.unknown_imports
        assert "langchain_openai" in resp.detected_dependencies

    def test_query_alone_does_not_match_sql(self):
        """A tool with 'query' in its name but no SQL context should NOT get sql_generation."""
        source = '''
from langchain.tools import tool

@tool
def query_notion_database(filter: str = "") -> dict:
    """Query a Notion database and return page entries."""
    return {"results": [], "filter": filter}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        tools = resp.manifest_json.get("capabilities", {}).get("tools", [])
        for t in tools:
            assert t.get("capability_id") != "sql_generation", (
                f"Tool '{t['name']}' incorrectly matched sql_generation"
            )

    def test_weather_tool_gets_web_search_capability(self):
        """A weather API tool should get web_search, not code_analysis fallback."""
        source = '''
from langchain.tools import tool
import requests

@tool
def get_weather(city: str) -> dict:
    """Get current weather forecast for a city."""
    resp = requests.get(f"https://api.example.com/weather?city={city}")
    return resp.json()
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        tools = resp.manifest_json.get("capabilities", {}).get("tools", [])
        assert tools[0].get("capability_id") == "web_search"

    def test_stock_tool_gets_web_search_capability(self):
        """A stock/finance tool should get web_search, not code_analysis fallback."""
        source = '''
from crewai_tools import tool

@tool("Stock Price Lookup")
def get_stock_quote(symbol: str) -> dict:
    """Get the latest stock quote for a ticker symbol."""
    return {"symbol": symbol, "price": 150.0}
'''
        resp = convert(ConvertRequest(platform="crewai", content=source))
        tools = resp.manifest_json.get("capabilities", {}).get("tools", [])
        assert tools[0].get("capability_id") == "web_search"

    def test_calculator_gets_code_analysis(self):
        """A math/calculate tool should get code_analysis."""
        source = '''
from langchain.tools import tool

@tool
def calculate(expression: str) -> dict:
    """Evaluate a mathematical expression."""
    return {"result": eval(expression)}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        tools = resp.manifest_json.get("capabilities", {}).get("tools", [])
        assert tools[0].get("capability_id") == "code_analysis"


# ── Sprint D: Hardcoded Credentials Detection ──────────────────────


class TestHardcodedCredentials:
    """Tests for hardcoded credential detection."""

    def test_hardcoded_api_key_blocks_draft(self):
        """A tool with a hardcoded API key should be low + not draft_ready."""
        source = '''
from langchain.tools import tool
import requests

API_KEY = "sk-abc123def456ghi789jkl012mno345pqr"

@tool
def search(query: str) -> dict:
    """Search."""
    return requests.get("https://api.example.com", headers={"Authorization": API_KEY}).json()
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        assert resp.confidence.level == "low"
        assert resp.draft_ready is False
        cred_warnings = [w for w in resp.warnings if "hardcoded credential" in w.lower()]
        assert len(cred_warnings) >= 1
        blocking = [w for w in resp.grouped_warnings if w.category == "blocking" and "credential" in w.message.lower()]
        assert len(blocking) >= 1

    def test_hardcoded_bearer_token_blocks_draft(self):
        """Bearer token prefix should be detected."""
        source = '''
from langchain.tools import tool

AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxx"

@tool
def fetch(url: str) -> dict:
    """Fetch."""
    return {"url": url}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        assert resp.confidence.level == "low"
        assert resp.draft_ready is False

    def test_hardcoded_aws_key_blocks_draft(self):
        """AWS access key prefix should be detected."""
        source = '''
from langchain.tools import tool

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"

@tool
def s3_list(bucket: str) -> dict:
    """List S3."""
    return {"bucket": bucket}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        assert resp.confidence.level == "low"
        assert resp.draft_ready is False

    def test_empty_string_not_flagged(self):
        """Empty string or short placeholder should NOT be flagged."""
        source = '''
from langchain.tools import tool

API_KEY = ""
SECRET_KEY = "xxx"

@tool
def fetch(url: str) -> dict:
    """Fetch."""
    return {"url": url}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        cred_warnings = [w for w in resp.warnings if "hardcoded credential" in w.lower()]
        assert len(cred_warnings) == 0
        assert resp.confidence.level == "high"

    def test_env_var_lookup_not_flagged(self):
        """os.getenv() patterns should NOT trigger credential detection."""
        source = '''
from langchain.tools import tool
import os

API_KEY = os.getenv("MY_API_KEY", "")

@tool
def search(query: str) -> dict:
    """Search."""
    return {"query": query}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        cred_warnings = [w for w in resp.warnings if "hardcoded credential" in w.lower()]
        assert len(cred_warnings) == 0

    def test_non_credential_variable_not_flagged(self):
        """Variables without credential-like names should NOT be flagged."""
        source = '''
from langchain.tools import tool

BASE_URL = "https://api.example.com/v1/search/endpoint"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

@tool
def fetch(url: str) -> dict:
    """Fetch."""
    return {"url": url}
'''
        resp = convert(ConvertRequest(platform="langchain", content=source))
        cred_warnings = [w for w in resp.warnings if "hardcoded credential" in w.lower()]
        assert len(cred_warnings) == 0
        assert resp.confidence.level == "high"

    def test_invariant_no_credential_passes_draft_ready(self):
        """Invariant: hardcoded credential must NEVER be draft_ready=true."""
        fixtures = [
            # sk- prefix
            '''
from langchain.tools import tool
API_KEY = "sk-1234567890abcdef1234567890abcdef"
@tool
def run(q: str) -> dict:
    return {"q": q}
''',
            # ghp_ prefix (GitHub token)
            '''
from langchain.tools import tool
ACCESS_TOKEN = "ghp_ABCDEFghijklmnopqrstuvwxyz012345"
@tool
def run(q: str) -> dict:
    return {"q": q}
''',
            # Long string with credential name
            '''
from crewai_tools import tool
CLIENT_SECRET = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
@tool("Test")
def run(q: str) -> dict:
    return {"q": q}
''',
        ]
        for fixture in fixtures:
            resp = convert(ConvertRequest(
                platform="langchain" if "langchain" in fixture else "crewai",
                content=fixture,
            ))
            assert resp.draft_ready is False, (
                f"Hardcoded credential fixture was draft_ready=True! "
                f"Warnings: {resp.warnings}"
            )
