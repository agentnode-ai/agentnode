"""Semantic search using sentence-transformers and cosine similarity."""

from __future__ import annotations


def run(
    query: str,
    documents: list[str],
    model_name: str = "all-MiniLM-L6-v2",
    top_k: int = 5,
) -> dict:
    """Find the most semantically similar documents to a query.

    Args:
        query: The search query.
        documents: List of document strings to search over.
        model_name: Sentence-transformer model name.
        top_k: Number of top results to return.

    Returns:
        dict with results (list of {document, score, index}) and model.
    """
    import numpy as np
    from sentence_transformers import SentenceTransformer

    if not documents:
        return {"results": [], "model": model_name}

    top_k = min(top_k, len(documents))

    model = SentenceTransformer(model_name)

    # Encode query and documents
    query_embedding = model.encode([query], normalize_embeddings=True)[0]
    doc_embeddings = model.encode(documents, normalize_embeddings=True)

    # Compute cosine similarity (embeddings are already normalised, so dot product suffices)
    similarities = np.dot(doc_embeddings, query_embedding)

    # Get top-k indices sorted by descending similarity
    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        results.append({
            "document": documents[int(idx)],
            "score": round(float(similarities[idx]), 6),
            "index": int(idx),
        })

    return {
        "results": results,
        "model": model_name,
    }
