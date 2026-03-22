"""Add blog_post_types table, post_type_id and previous_url to blog_posts.

Revision ID: 010
Revises: 009
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE blog_post_types (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            slug VARCHAR(100) NOT NULL UNIQUE,
            url_prefix VARCHAR(100) NOT NULL UNIQUE,
            description VARCHAR(300),
            icon VARCHAR(50) NOT NULL DEFAULT 'article',
            has_archive BOOLEAN NOT NULL DEFAULT true,
            is_system BOOLEAN NOT NULL DEFAULT false,
            archive_title VARCHAR(200),
            archive_description VARCHAR(500),
            sitemap_priority NUMERIC(2,1) NOT NULL DEFAULT 0.6,
            sitemap_changefreq VARCHAR(20) NOT NULL DEFAULT 'weekly',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("ALTER TABLE blog_posts ADD COLUMN post_type_id UUID REFERENCES blog_post_types(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE blog_posts ADD COLUMN previous_url VARCHAR(500)")
    op.execute("CREATE INDEX ix_blog_posts_post_type_id ON blog_posts (post_type_id)")

    # Seed system post types
    op.execute("""
        INSERT INTO blog_post_types (name, slug, url_prefix, description, is_system, archive_title, archive_description, sitemap_priority, sitemap_changefreq, sort_order) VALUES
            ('Post',       'post',       'blog',         'Standard blog posts',          true,  'Blog',         'Latest articles and insights from AgentNode.',          0.6, 'weekly',  0),
            ('Tutorial',   'tutorial',   'tutorials',    'Step-by-step guides',          true,  'Tutorials',    'Step-by-step guides for building with AgentNode.',      0.7, 'monthly', 1),
            ('Changelog',  'changelog',  'changelog',    'Product updates and releases', true,  'Changelog',    'Product updates, new features, and release notes.',     0.5, 'weekly',  2),
            ('Case Study', 'case-study', 'case-studies', 'Real-world usage examples',    true,  'Case Studies', 'Real-world examples of agents powered by AgentNode.',   0.6, 'monthly', 3)
    """)

    # Set all existing posts to "post" type, then add NOT NULL
    op.execute("UPDATE blog_posts SET post_type_id = (SELECT id FROM blog_post_types WHERE slug = 'post')")
    op.execute("ALTER TABLE blog_posts ALTER COLUMN post_type_id SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE blog_posts DROP COLUMN IF EXISTS previous_url")
    op.execute("ALTER TABLE blog_posts DROP COLUMN IF EXISTS post_type_id")
    op.execute("DROP TABLE IF EXISTS blog_post_types")
