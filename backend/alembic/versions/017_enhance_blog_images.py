"""Add title, original_filename, caption to blog_images.

Revision ID: 017
Revises: 016
"""
from alembic import op
import sqlalchemy as sa


revision = "017"
down_revision = "016"


def upgrade() -> None:
    op.add_column("blog_images", sa.Column("title", sa.VARCHAR(255), nullable=True))
    op.add_column("blog_images", sa.Column("original_filename", sa.VARCHAR(255), nullable=True))
    op.add_column("blog_images", sa.Column("caption", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("blog_images", "caption")
    op.drop_column("blog_images", "original_filename")
    op.drop_column("blog_images", "title")
