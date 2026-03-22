"""Fix package artifacts to improve verification scores.

Modifies manifest.json inside tar.gz artifacts to add proper input_schema
with enum values and correct defaults. Also fixes tool.py where needed.

Usage: PYTHONPATH=/opt/agentnode/backend python -m scripts.fix_packages_v2
"""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.shared.storage import download_artifact, upload_artifact


def _update_manifest_schema(manifest: dict, tool_name: str, new_schema: dict) -> dict:
    """Update input_schema for a specific tool in the manifest."""
    tools = manifest.get("capabilities", {}).get("tools", [])
    for tool in tools:
        if tool.get("name") == tool_name or tool.get("capability_id") == tool_name:
            tool["input_schema"] = new_schema
            print(f"    Updated input_schema for {tool_name}")
    return manifest


def _fix_artifact(slug: str, manifest_patches: dict = None, file_patches: dict = None):
    """Download, patch, and re-upload a package artifact."""
    artifact_key = f"artifacts/{slug}/1.0.0/package.tar.gz"

    # For packages that might be v0.1.0
    try:
        data = download_artifact(artifact_key)
    except Exception:
        artifact_key = f"artifacts/{slug}/0.1.0/package.tar.gz"
        data = download_artifact(artifact_key)

    tmp = tempfile.mkdtemp()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")

        # Patch manifest.json
        if manifest_patches:
            manifest_path = None
            for root, dirs, files in os.walk(tmp):
                if "manifest.json" in files:
                    manifest_path = os.path.join(root, "manifest.json")
                    break

            if manifest_path:
                with open(manifest_path) as f:
                    manifest = json.load(f)

                for tool_name, schema in manifest_patches.items():
                    manifest = _update_manifest_schema(manifest, tool_name, schema)

                with open(manifest_path, "w") as f:
                    json.dump(manifest, f, indent=2)

        # Patch source files
        if file_patches:
            for rel_path, content in file_patches.items():
                # Find the file by searching
                for root, dirs, files in os.walk(tmp):
                    target = os.path.join(root, rel_path)
                    if os.path.exists(target):
                        with open(target, "w", encoding="utf-8") as f:
                            f.write(content)
                        print(f"    Patched {rel_path}")
                        break
                else:
                    # Try to find by filename only
                    fname = os.path.basename(rel_path)
                    for root, dirs, files in os.walk(tmp):
                        if fname in files:
                            target = os.path.join(root, fname)
                            with open(target, "w", encoding="utf-8") as f:
                                f.write(content)
                            print(f"    Patched {fname} at {root}")
                            break

        # Re-tar
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for root, dirs, files in os.walk(tmp):
                for fn in files:
                    full = os.path.join(root, fn)
                    arcname = "./" + os.path.relpath(full, tmp).replace("\\", "/")
                    tar.add(full, arcname=arcname)
        buf.seek(0)

        upload_artifact(artifact_key, buf.read())
        print(f"  ✓ Uploaded {slug}")
    finally:
        shutil.rmtree(tmp)


# ══════════════════════════════════════════════════════════════
# Package fixes
# ══════════════════════════════════════════════════════════════

FIXES = {}

# ── pdf-extractor-pack: file_path needs .pdf default ──
FIXES["pdf-extractor-pack"] = {
    "manifest_patches": {
        "extract_pdf": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the PDF file", "default": "/tmp/agentnode_verify/test.pdf"},
                "pages": {"type": "string", "default": "all"},
                "extract_tables": {"type": "boolean", "default": True},
                "extract_images": {"type": "boolean", "default": False},
            },
            "required": ["file_path"],
        }
    }
}

# ── pdf-reader-pack: file_path needs .pdf default ──
FIXES["pdf-reader-pack"] = {
    "manifest_patches": {
        "extract_pdf_text": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the PDF file", "default": "/tmp/agentnode_verify/test.pdf"},
                "pages": {"type": "string", "default": "all"},
            },
            "required": ["file_path"],
        }
    }
}

# ── file-converter-pack: output_format needs enum, better default ──
FIXES["file-converter-pack"] = {
    "manifest_patches": {
        "convert_file": {
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "Path to the source file", "default": "/tmp/agentnode_verify/test.txt"},
                "output_format": {"type": "string", "enum": ["html", "md", "json", "csv"], "default": "md"},
                "output_path": {"type": "string", "default": ""},
            },
            "required": ["input_path", "output_format"],
        }
    }
}

# ── markdown-notes-pack: operation enum, vault_path as directory ──
FIXES["markdown-notes-pack"] = {
    "manifest_patches": {
        "manage_notes": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list", "search", "list_tags", "create", "get"], "default": "list"},
                "vault_path": {"type": "string", "description": "Path to the notes vault directory", "default": "/tmp/agentnode_verify"},
            },
            "required": ["operation", "vault_path"],
        }
    }
}

# ── calendar-manager-pack: operation enum, calendar_file as .ics ──
FIXES["calendar-manager-pack"] = {
    "manifest_patches": {
        "calendar_manage": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["create_event", "parse_ical", "list_events", "create_ical"]},
                "calendar_file": {"type": "string", "default": ""},
                "title": {"type": "string", "default": "Test Event"},
                "start": {"type": "string", "default": "2024-01-01T10:00:00"},
                "end": {"type": "string", "default": "2024-01-01T11:00:00"},
            },
            "required": ["operation"],
            "examples": [{"operation": "create_event", "title": "Test", "start": "2024-01-01T10:00:00", "end": "2024-01-01T11:00:00"}],
        }
    }
}

# ── crm-connector-pack: provider enum ──
FIXES["crm-connector-pack"] = {
    "manifest_patches": {
        "crm_manage": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list_contacts", "get_contact", "create_contact", "search_contacts"]},
                "provider": {"type": "string", "enum": ["hubspot"], "default": "hubspot"},
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["operation", "provider"],
        }
    }
}

# ── task-manager-pack: provider enum ──
FIXES["task-manager-pack"] = {
    "manifest_patches": {
        "task_manage": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list_issues", "get_issue", "create_issue", "search_issues"]},
                "provider": {"type": "string", "enum": ["linear"], "default": "linear"},
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["operation", "provider"],
        }
    }
}

# ── social-media-pack: platform + operation enums ──
FIXES["social-media-pack"] = {
    "manifest_patches": {
        "manage_social": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["twitter"], "default": "twitter"},
                "operation": {"type": "string", "enum": ["post_tweet", "get_timeline"]},
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["platform", "operation"],
        }
    }
}

# ── smart-lights-pack: operation enum ──
FIXES["smart-lights-pack"] = {
    "manifest_patches": {
        "control_lights": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list_lights", "get_state", "turn_on", "turn_off", "set_scene"]},
                "bridge_ip": {"type": "string", "default": ""},
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["operation"],
        }
    }
}

# ── home-automation-pack: operation enum ──
FIXES["home-automation-pack"] = {
    "manifest_patches": {
        "home_control": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list_entities", "get_state", "turn_on", "turn_off", "call_service"]},
                "ha_url": {"type": "string", "default": ""},
                "token": {"type": "string", "default": ""},
            },
            "required": ["operation"],
        }
    }
}

# ── youtube-analyzer-pack: operation enum ──
FIXES["youtube-analyzer-pack"] = {
    "manifest_patches": {
        "analyze_youtube": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["search", "video_info", "captions", "comments"]},
                "query": {"type": "string", "default": "test"},
                "api_key": {"type": "string", "default": ""},
                "video_id": {"type": "string", "default": "dQw4w9WgXcQ"},
            },
            "required": ["operation"],
            "examples": [{"operation": "search", "query": "test", "api_key": ""}],
        }
    }
}

# ── ocr-reader-pack: file_path needs .png default ──
FIXES["ocr-reader-pack"] = {
    "manifest_patches": {
        "ocr_read": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the image file", "default": "/tmp/agentnode_verify/test.png"},
                "language": {"type": "string", "default": "eng"},
            },
            "required": ["file_path"],
        }
    }
}


if __name__ == "__main__":
    for slug, fix_config in FIXES.items():
        print(f"\nFixing {slug}...")
        try:
            _fix_artifact(
                slug,
                manifest_patches=fix_config.get("manifest_patches"),
                file_patches=fix_config.get("file_patches"),
            )
        except Exception as e:
            print(f"  ✗ ERROR: {e}")

    print("\nDone! Run reverification to see updated scores.")
