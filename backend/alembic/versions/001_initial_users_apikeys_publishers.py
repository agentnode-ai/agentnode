"""Initial: users, api_keys, publishers

Revision ID: 001
Revises: None
Create Date: 2026-03-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute("CREATE TYPE trust_level AS ENUM ('unverified', 'verified', 'trusted', 'curated')")

    op.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_email_verified BOOLEAN NOT NULL DEFAULT FALSE,
            two_factor_secret TEXT,
            two_factor_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_users_email ON users(email)")
    op.execute("CREATE INDEX idx_users_username ON users(username)")

    op.execute("""
        CREATE TABLE api_keys (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key_prefix TEXT NOT NULL,
            key_hash_sha256 TEXT NOT NULL,
            label TEXT,
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_api_keys_user ON api_keys(user_id)")
    op.execute("CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix)")

    op.execute("""
        CREATE TABLE publishers (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            bio TEXT,
            website_url TEXT,
            github_url TEXT,
            trust_level trust_level NOT NULL DEFAULT 'unverified',
            is_suspended BOOLEAN NOT NULL DEFAULT FALSE,
            suspension_reason TEXT,
            packages_published_count INTEGER NOT NULL DEFAULT 0,
            packages_cleared_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_publishers_slug ON publishers(slug)")

    op.execute("""
        CREATE TABLE capability_taxonomy (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            description TEXT,
            category TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS capability_taxonomy")
    op.execute("DROP TABLE IF EXISTS publishers")
    op.execute("DROP TABLE IF EXISTS api_keys")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TYPE IF EXISTS trust_level")
