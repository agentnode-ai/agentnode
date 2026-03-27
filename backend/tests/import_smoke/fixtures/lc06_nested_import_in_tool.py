from langchain.tools import tool


@tool
def hash_text(text: str, algorithm: str = "sha256") -> dict:
    """Compute a hash of the input text."""
    import hashlib
    h = hashlib.new(algorithm)
    h.update(text.encode("utf-8"))
    return {"hash": h.hexdigest(), "algorithm": algorithm, "length": len(text)}
