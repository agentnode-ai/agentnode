"""Fix word-counter-pack: make count_words accept both kwargs and dict input."""
import io
import os
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.shared.storage import download_artifact, upload_artifact

NEW_TOOL = '''"""Word Counter — count words, characters, sentences in text."""


def count_words(text: str = "", **kwargs) -> dict:
    """Count words, characters, and sentences in the given text.

    Accepts either:
      - count_words(text="Hello world")
      - count_words({"text": "Hello world"})  (legacy dict input)
    """
    # Support legacy dict-style input
    if isinstance(text, dict):
        inputs = text
        text = inputs.get("text", "")

    words = len(text.split()) if text.strip() else 0
    characters = len(text)
    sentences = sum(1 for c in text if c in ".!?") or (1 if text.strip() else 0)
    return {
        "words": words,
        "characters": characters,
        "sentences": sentences,
        "text_preview": text[:100] + ("..." if len(text) > 100 else ""),
    }
'''

slug = "word-counter-pack"
artifact_key = f"artifacts/{slug}/1.0.0/package.tar.gz"
data = download_artifact(artifact_key)

tmp = tempfile.mkdtemp()
try:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(tmp, filter="data")

    tool_path = os.path.join(tmp, "src", "word_counter_pack", "tool.py")
    with open(tool_path, "w", encoding="utf-8") as f:
        f.write(NEW_TOOL)
    print(f"Fixed {tool_path}")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(tmp):
            for fn in files:
                full = os.path.join(root, fn)
                arcname = "./" + os.path.relpath(full, tmp).replace("\\", "/")
                tar.add(full, arcname=arcname)
    buf.seek(0)
    upload_artifact(artifact_key, buf.read())
    print(f"Uploaded {slug}")
finally:
    shutil.rmtree(tmp)

print("Done!")
