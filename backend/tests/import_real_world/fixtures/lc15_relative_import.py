from langchain.tools import tool
from .utils import clean_html, extract_links


@tool
def scrape_page(url: str) -> dict:
    """Scrape a web page and extract clean text and links."""
    import requests
    resp = requests.get(url)
    text = clean_html(resp.text)
    links = extract_links(resp.text)
    return {"text": text, "links": links}
