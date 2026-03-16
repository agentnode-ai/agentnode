"""Text embedding generation tool using sentence-transformers."""

from __future__ import annotations

from sentence_transformers import SentenceTransformer


def run(
    texts: list[str] | str,
    model_name: str = "all-MiniLM-L6-v2",
) -> dict:
    """Generate embeddings for one or more texts.

    Args:
        texts: A single string or list of strings to embed.
        model_name: The sentence-transformers model to use.

    Returns:
        A dict with embeddings, model name, and dimensions.
    """
    if isinstance(texts, str):
        texts = [texts]

    model = SentenceTransformer(model_name)
    embeddings = model.encode(texts, convert_to_numpy=True)

    # Convert numpy arrays to lists for JSON serialization
    embeddings_list = [emb.tolist() for emb in embeddings]
    dimensions = len(embeddings_list[0]) if embeddings_list else 0

    return {
        "embeddings": embeddings_list,
        "model": model_name,
        "dimensions": dimensions,
        "num_texts": len(texts),
    }
