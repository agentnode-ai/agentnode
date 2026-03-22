"""Update Capability.input_schema in DB with proper enum values.

These packages had null input_schema, causing the auto-generated
schema (without enums) to be used during verification.

Usage: PYTHONPATH=/opt/agentnode/backend .venv/bin/python -m scripts.fix_schemas_db
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from sqlalchemy import text

import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401
import app.verification.models  # noqa: F401

SCHEMA_UPDATES = {
    "calendar-manager-pack": {
        "calendar_manage": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["create_event", "parse_ical", "list_events", "create_ical"],
                },
                "calendar_file": {"type": "string", "default": ""},
                "title": {"type": "string", "default": "Test Event"},
                "start": {"type": "string", "default": "2024-01-01T10:00:00"},
                "end": {"type": "string", "default": "2024-01-01T11:00:00"},
            },
            "required": ["operation"],
            "examples": [
                {
                    "operation": "create_event",
                    "title": "Test",
                    "start": "2024-01-01T10:00:00",
                    "end": "2024-01-01T11:00:00",
                }
            ],
        }
    },
    "crm-connector-pack": {
        "crm_manage": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "list_contacts",
                        "get_contact",
                        "create_contact",
                        "search_contacts",
                    ],
                },
                "provider": {
                    "type": "string",
                    "enum": ["hubspot"],
                    "default": "hubspot",
                },
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["operation", "provider"],
        }
    },
    "file-converter-pack": {
        "convert_file": {
            "type": "object",
            "properties": {
                "input_path": {
                    "type": "string",
                    "description": "Path to the source file",
                    "default": "/tmp/agentnode_verify/test.txt",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["html", "md", "json", "csv"],
                    "default": "md",
                },
                "output_path": {"type": "string", "default": ""},
            },
            "required": ["input_path", "output_format"],
        }
    },
    "home-automation-pack": {
        "home_control": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "list_entities",
                        "get_state",
                        "turn_on",
                        "turn_off",
                        "call_service",
                    ],
                },
                "ha_url": {"type": "string", "default": ""},
                "token": {"type": "string", "default": ""},
            },
            "required": ["operation"],
        }
    },
    "smart-lights-pack": {
        "control_lights": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "list_lights",
                        "get_state",
                        "turn_on",
                        "turn_off",
                        "set_scene",
                    ],
                },
                "bridge_ip": {"type": "string", "default": ""},
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["operation"],
        }
    },
    "social-media-pack": {
        "manage_social": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["twitter"],
                    "default": "twitter",
                },
                "operation": {
                    "type": "string",
                    "enum": ["post_tweet", "get_timeline"],
                },
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["platform", "operation"],
        }
    },
    "task-manager-pack": {
        "task_manage": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "list_issues",
                        "get_issue",
                        "create_issue",
                        "search_issues",
                    ],
                },
                "provider": {
                    "type": "string",
                    "enum": ["linear"],
                    "default": "linear",
                },
                "api_key": {"type": "string", "default": ""},
            },
            "required": ["operation", "provider"],
        }
    },
    "youtube-analyzer-pack": {
        "analyze_youtube": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["search", "video_info", "captions", "comments"],
                },
                "query": {"type": "string", "default": "test"},
                "api_key": {"type": "string", "default": ""},
                "video_id": {"type": "string", "default": "dQw4w9WgXcQ"},
            },
            "required": ["operation"],
            "examples": [
                {"operation": "search", "query": "test", "api_key": ""}
            ],
        }
    },
    "markdown-notes-pack": {
        "manage_notes": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list", "search", "list_tags", "create", "get"],
                    "default": "list",
                },
                "vault_path": {
                    "type": "string",
                    "description": "Path to the notes vault directory",
                    "default": "/tmp/agentnode_verify",
                },
            },
            "required": ["operation", "vault_path"],
        }
    },
    "pdf-extractor-pack": {
        "extract_pdf": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the PDF file",
                    "default": "/tmp/agentnode_verify/test.pdf",
                },
                "pages": {"type": "string", "default": "all"},
                "extract_tables": {"type": "boolean", "default": True},
                "extract_images": {"type": "boolean", "default": False},
            },
            "required": ["file_path"],
        }
    },
    "pdf-reader-pack": {
        "extract_pdf_text": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the PDF file",
                    "default": "/tmp/agentnode_verify/test.pdf",
                },
                "pages": {"type": "string", "default": "all"},
            },
            "required": ["file_path"],
        }
    },
    "ocr-reader-pack": {
        "ocr_read": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the image file",
                    "default": "/tmp/agentnode_verify/test.png",
                },
                "language": {"type": "string", "default": "eng"},
            },
            "required": ["file_path"],
        }
    },
}


async def main():
    async with async_session_factory() as session:
        updated = 0
        for slug, tool_schemas in SCHEMA_UPDATES.items():
            for tool_name, schema in tool_schemas.items():
                result = await session.execute(
                    text(
                        """
                    UPDATE capabilities c
                    SET input_schema = CAST(:schema AS jsonb)
                    FROM package_versions pv
                    JOIN packages p ON p.id = pv.package_id
                    WHERE c.package_version_id = pv.id
                    AND c.name = :tool_name
                    AND p.slug = :slug
                """
                    ),
                    {
                        "schema": json.dumps(schema),
                        "tool_name": tool_name,
                        "slug": slug,
                    },
                )
                if result.rowcount > 0:
                    print(f"  Updated {slug}/{tool_name}: {result.rowcount} row(s)")
                    updated += 1
                else:
                    print(f"  MISS {slug}/{tool_name}")
        await session.commit()
        print(f"\nTotal updated: {updated}")


if __name__ == "__main__":
    asyncio.run(main())
