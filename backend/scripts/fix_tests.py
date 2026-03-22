"""Fix test files in word-counter-pack and webpage-extractor-pack artifacts."""
import io
import os
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.shared.storage import download_artifact, upload_artifact

WORD_COUNTER_TEST = '''from word_counter_pack.tool import count_words


def test_count_words():
    result = count_words({"text": "Hello world. This is a test."})
    assert result["words"] == 6
    assert result["sentences"] == 2
    assert result["characters"] == 28


def test_empty():
    result = count_words({"text": ""})
    assert result["words"] == 0
'''

WEBPAGE_EXTRACTOR_TEST = '''"""Tests for webpage-extractor-pack."""


def test_run_returns_structure():
    """Test extraction returns expected dict structure."""
    from webpage_extractor_pack.tool import run

    result = run("https://this-does-not-exist-99999.invalid")
    assert "title" in result
    assert "text" in result
    assert "url" in result
    assert result["url"] == "https://this-does-not-exist-99999.invalid"


def test_run_bad_url():
    """Test graceful handling of unreachable URL."""
    from webpage_extractor_pack.tool import run

    result = run("https://this-does-not-exist-99999.invalid")
    assert "error" in result
    assert result["text"] == ""
'''

FIXES = {
    "word-counter-pack": {
        "./tests/test_count.py": WORD_COUNTER_TEST,
    },
    "webpage-extractor-pack": {
        "./tests/test_tool.py": WEBPAGE_EXTRACTOR_TEST,
    },
}

for slug, file_fixes in FIXES.items():
    artifact_key = f"artifacts/{slug}/1.0.0/package.tar.gz"
    data = download_artifact(artifact_key)

    tmp = tempfile.mkdtemp()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")

        for path, content in file_fixes.items():
            full_path = os.path.join(tmp, path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  Fixed {slug}: {path}")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for root, dirs, files in os.walk(tmp):
                for fn in files:
                    full = os.path.join(root, fn)
                    arcname = "./" + os.path.relpath(full, tmp).replace("\\", "/")
                    tar.add(full, arcname=arcname)
        buf.seek(0)

        upload_artifact(artifact_key, buf.read())
        print(f"  Uploaded {slug}")
    finally:
        shutil.rmtree(tmp)

print("Done!")
