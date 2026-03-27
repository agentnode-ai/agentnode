"""
Tool file that uses relative imports.
People copy-paste this from a package structure where .utils exists — but the importer
can't resolve relative imports. Should break on import.
"""

from langchain.tools import tool

# relative import — will fail when loaded as a standalone file
from .utils import format_output, validate_input
from .config import DEFAULT_TIMEOUT, API_BASE_URL


@tool
def lookup_company(company_name: str) -> dict:
    """
    Look up information about a company by name.

    Args:
        company_name: The name of the company to look up

    Returns:
        dict with company details
    """
    validated = validate_input(company_name)
    if not validated:
        return {"error": "Invalid company name", "company": company_name}

    import requests
    try:
        resp = requests.get(
            f"{API_BASE_URL}/companies/search",
            params={"q": company_name},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()
        return format_output(raw)
    except Exception as e:
        return {"error": str(e), "company": company_name}
