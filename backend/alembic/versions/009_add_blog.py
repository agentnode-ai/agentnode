"""Add blog_categories, blog_posts, and blog_images tables.

Revision ID: 009
Revises: 008
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enum for post status
    op.execute("CREATE TYPE blog_post_status AS ENUM ('draft', 'published', 'archived')")

    # Categories table
    op.execute("""
        CREATE TABLE blog_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            slug VARCHAR(100) NOT NULL UNIQUE,
            description VARCHAR(300),
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Posts table
    op.execute("""
        CREATE TABLE blog_posts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(255) NOT NULL,
            slug VARCHAR(255) NOT NULL UNIQUE,
            content_json JSONB,
            content_html TEXT,
            excerpt VARCHAR(500),
            cover_image_url VARCHAR(500),
            cover_image_alt VARCHAR(255),
            seo_title VARCHAR(70),
            seo_description VARCHAR(170),
            og_image_url VARCHAR(500),
            category_id UUID REFERENCES blog_categories(id) ON DELETE SET NULL,
            tags JSONB DEFAULT '[]'::jsonb,
            author_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status blog_post_status NOT NULL DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            reading_time_min INTEGER,
            is_featured BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_blog_posts_slug ON blog_posts (slug)")
    op.execute("CREATE INDEX ix_blog_posts_status ON blog_posts (status)")
    op.execute("CREATE INDEX ix_blog_posts_category_id ON blog_posts (category_id)")
    op.execute("CREATE INDEX ix_blog_posts_published_at ON blog_posts (published_at DESC)")

    # Images table
    op.execute("""
        CREATE TABLE blog_images (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id UUID REFERENCES blog_posts(id) ON DELETE SET NULL,
            object_key VARCHAR(500) NOT NULL,
            url VARCHAR(500) NOT NULL,
            alt_text VARCHAR(255),
            file_size INTEGER,
            width INTEGER,
            height INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_blog_images_post_id ON blog_images (post_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS blog_images")
    op.execute("DROP TABLE IF EXISTS blog_posts")
    op.execute("DROP TABLE IF EXISTS blog_categories")
    op.execute("DROP TYPE IF EXISTS blog_post_status")
