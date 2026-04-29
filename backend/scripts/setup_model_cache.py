"""One-time setup: download ML models for offline verification.

Run once on the verification host to pre-populate the model cache.
Models are then mounted read-only into verification containers.

Usage:
    python backend/scripts/setup_model_cache.py
    # or with custom cache dir:
    AGENTNODE_MODEL_CACHE=/path/to/cache python backend/scripts/setup_model_cache.py
"""

import os
import sys


CACHE_DIR = os.environ.get("AGENTNODE_MODEL_CACHE", "/opt/agentnode/model-cache")


def setup_huggingface():
    hf_dir = os.path.join(CACHE_DIR, "huggingface")
    os.makedirs(hf_dir, exist_ok=True)
    os.environ["HF_HOME"] = hf_dir

    try:
        from sentence_transformers import SentenceTransformer
        print("Downloading sentence-transformers model: all-MiniLM-L6-v2 ...")
        SentenceTransformer("all-MiniLM-L6-v2")
        print("  -> cached.")
    except ImportError:
        print("  -> sentence-transformers not installed, skipping.")


def setup_whisper():
    whisper_dir = os.path.join(CACHE_DIR, "whisper")
    os.makedirs(whisper_dir, exist_ok=True)

    try:
        import whisper
        print("Downloading whisper model: base ...")
        whisper.load_model("base", download_root=whisper_dir)
        print("  -> cached.")
    except ImportError:
        print("  -> openai-whisper not installed, skipping.")


def setup_torch():
    torch_dir = os.path.join(CACHE_DIR, "torch")
    os.makedirs(torch_dir, exist_ok=True)
    print(f"Torch cache dir created: {torch_dir}")


def main():
    print(f"Model cache directory: {CACHE_DIR}")
    os.makedirs(CACHE_DIR, exist_ok=True)

    setup_huggingface()
    setup_whisper()
    setup_torch()

    print(f"\nDone. Models cached at {CACHE_DIR}")
    print("Mount into containers with:")
    print(f"  -v {CACHE_DIR}/huggingface:/home/verifier/.cache/huggingface:ro")
    print(f"  -v {CACHE_DIR}/whisper:/home/verifier/.cache/whisper:ro")
    print(f"  -v {CACHE_DIR}/torch:/home/verifier/.cache/torch:ro")


if __name__ == "__main__":
    main()
