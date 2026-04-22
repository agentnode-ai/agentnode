import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.shared.models import Base

# Import all models so Alembic sees them
from app.auth.models import ApiKey, User  # noqa: F401
from app.publishers.models import Publisher  # noqa: F401
from app.packages.models import (  # noqa: F401
    Package, PackageVersion, Capability, PackageTag, PackageCategory,
    CompatibilityRule, Dependency, Permission, UpgradeMetadata,
    SecurityFinding, CapabilityTaxonomy,
    Installation, Review, PackageReport,
)
from app.verification.models import VerificationResult  # noqa: F401
from app.blog.models import BlogCategory, BlogPost, BlogImage, BlogPostType  # noqa: F401
from app.sitemap.models import SitemapPage  # noqa: F401
from app.admin.models import AdminAuditLog, SystemSetting  # noqa: F401
from app.billing.models import ReviewRequest, ProcessedStripeEvent  # noqa: F401
from app.webhooks.models import Webhook, WebhookDelivery  # noqa: F401
from app.invites.models import ImportCandidate, InviteCode, CandidateEvent  # noqa: F401
from app.credentials.models import CredentialStore  # noqa: F401
from app.support.models import SupportTicket, SupportMessage  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
