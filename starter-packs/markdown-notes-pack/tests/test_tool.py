"""Tests for markdown-notes-pack."""

import os
import tempfile

import pytest


@pytest.fixture
def vault_dir():
    """Create a temporary vault directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_create_note(vault_dir):
    """Test creating a new note."""
    from markdown_notes_pack.tool import run

    result = run("create", vault_dir, title="Test Note", content="Hello world", tags=["test", "demo"])

    assert result["status"] == "created"
    assert result["title"] == "Test Note"
    assert result["tags"] == ["test", "demo"]
    assert os.path.exists(result["path"])

    # Verify file content
    with open(result["path"], encoding="utf-8") as f:
        content = f.read()
    assert "# Test Note" in content
    assert "Hello world" in content
    assert "test" in content


def test_create_avoids_overwrite(vault_dir):
    """Test that creating a note with same title doesn't overwrite."""
    from markdown_notes_pack.tool import run

    r1 = run("create", vault_dir, title="Duplicate", content="First")
    r2 = run("create", vault_dir, title="Duplicate", content="Second")

    assert r1["path"] != r2["path"]
    assert os.path.exists(r1["path"])
    assert os.path.exists(r2["path"])


def test_list_notes(vault_dir):
    """Test listing notes."""
    from markdown_notes_pack.tool import run

    run("create", vault_dir, title="Note A", tags=["alpha"])
    run("create", vault_dir, title="Note B", tags=["beta"])

    result = run("list", vault_dir)

    assert result["total"] == 2
    titles = [n["title"] for n in result["notes"]]
    assert "Note A" in titles
    assert "Note B" in titles


def test_list_notes_filter_by_tag(vault_dir):
    """Test filtering notes by tag."""
    from markdown_notes_pack.tool import run

    run("create", vault_dir, title="Tagged", tags=["important"])
    run("create", vault_dir, title="Untagged", tags=["other"])

    result = run("list", vault_dir, tag="important")

    assert result["total"] == 1
    assert result["notes"][0]["title"] == "Tagged"


def test_get_note(vault_dir):
    """Test retrieving a specific note."""
    from markdown_notes_pack.tool import run

    run("create", vault_dir, title="Retrieve Me", content="Secret content", tags=["find"])

    result = run("get", vault_dir, title="Retrieve Me")

    assert result["title"] == "Retrieve Me"
    assert "Secret content" in result["content"]
    assert result["tags"] == ["find"]


def test_get_note_not_found(vault_dir):
    """Test getting a non-existent note returns error."""
    from markdown_notes_pack.tool import run

    result = run("get", vault_dir, title="Nonexistent")

    assert "error" in result


def test_search_notes(vault_dir):
    """Test searching notes by content."""
    from markdown_notes_pack.tool import run

    run("create", vault_dir, title="Python Tips", content="Use list comprehensions for cleaner code")
    run("create", vault_dir, title="Java Tips", content="Use streams for functional style")

    result = run("search", vault_dir, query="comprehensions")

    assert result["total"] == 1
    assert result["results"][0]["title"] == "Python Tips"


def test_search_case_insensitive(vault_dir):
    """Test that search is case-insensitive."""
    from markdown_notes_pack.tool import run

    run("create", vault_dir, title="CamelCase", content="AgentNode is great")

    result = run("search", vault_dir, query="agentnode")

    assert result["total"] == 1


def test_list_tags(vault_dir):
    """Test listing all tags with counts."""
    from markdown_notes_pack.tool import run

    run("create", vault_dir, title="A", tags=["python", "tutorial"])
    run("create", vault_dir, title="B", tags=["python", "advanced"])
    run("create", vault_dir, title="C", tags=["rust"])

    result = run("list_tags", vault_dir)

    assert result["total"] == 4
    tag_map = {t["tag"]: t["count"] for t in result["tags"]}
    assert tag_map["python"] == 2
    assert tag_map["tutorial"] == 1
    assert tag_map["advanced"] == 1
    assert tag_map["rust"] == 1


def test_unknown_operation(vault_dir):
    """Test that unknown operation raises ValueError."""
    from markdown_notes_pack.tool import run

    with pytest.raises(ValueError, match="Unknown operation"):
        run("delete", vault_dir)


def test_create_with_string_tags(vault_dir):
    """Test that comma-separated string tags work."""
    from markdown_notes_pack.tool import run

    result = run("create", vault_dir, title="String Tags", tags="a, b, c")

    assert result["tags"] == ["a", "b", "c"]
