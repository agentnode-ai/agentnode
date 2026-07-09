"""Complete the skill enum set: runtime 'none' + install_mode 'prompt_only'.

Skills (package_type='skill') use runtime='none' and install_mode='prompt_only'
per the validator matrix — migration 035 even says so in its docstring, but it
only extended package_type. The two other enums were never extended, so every
real skill publish failed with InvalidTextRepresentationError at the INSERT.

Revision ID: 041
Revises: 040
"""

from alembic import op
from sqlalchemy import text

revision = "041"
down_revision = "040"

_ADDITIONS = (
    ("runtime_type", "none"),
    ("install_mode", "prompt_only"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for type_name, value in _ADDITIONS:
        result = conn.execute(
            text(
                "SELECT 1 FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE pg_type.typname = :t AND pg_enum.enumlabel = :v"
            ),
            {"t": type_name, "v": value},
        )
        if result.scalar() is None:
            conn.execute(text(f"ALTER TYPE {type_name} ADD VALUE '{value}'"))


def downgrade() -> None:
    pass
