"""Add sitemap_pages table and seed with static pages.

Revision ID: 011
Revises: 010
"""
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sitemap_pages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            path VARCHAR(300) NOT NULL UNIQUE,
            priority NUMERIC(2,1) NOT NULL DEFAULT 0.5,
            changefreq VARCHAR(20) NOT NULL DEFAULT 'monthly',
            indexable BOOLEAN NOT NULL DEFAULT true,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        INSERT INTO sitemap_pages (path, priority, changefreq, indexable, sort_order) VALUES
            ('/',              1.0, 'daily',   true,  0),
            ('/search',        0.8, 'daily',   true,  1),
            ('/discover',      0.8, 'daily',   true,  2),
            ('/docs',          0.7, 'weekly',  true,  3),
            ('/for-developers',0.7, 'monthly', true,  4),
            ('/why-agentnode', 0.6, 'monthly', true,  5),
            ('/import',        0.6, 'monthly', true,  6),
            ('/publish',       0.6, 'monthly', true,  7),
            ('/builder',       0.7, 'weekly',  true,  8),
            ('/capabilities',  0.6, 'monthly', true,  9),
            ('/compare',       0.5, 'monthly', true, 10),
            ('/license',       0.3, 'yearly',  true, 11)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sitemap_pages")
