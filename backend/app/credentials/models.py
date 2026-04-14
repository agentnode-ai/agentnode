"""Credential storage models — per-user, per-connector encrypted credentials."""
from sqlalchemy import Column, Enum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import relationship

from app.shared.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CredentialStore(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Encrypted credential scoped to one user + one connector provider.

    Security invariants (S3, S8, S12):
    - encrypted_data is Fernet-encrypted JSON; raw secrets never in logs or responses
    - allowed_domains restricts which hosts this credential may contact
    - provider binds the credential to a specific service (e.g. "slack", "github")
    """

    __tablename__ = "credential_stores"
    __table_args__ = (
        UniqueConstraint("user_id", "connector_provider", name="uq_user_provider"),
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_provider = Column(Text, nullable=False, index=True)
    connector_package_slug = Column(Text, nullable=False)
    auth_type = Column(
        Enum("api_key", "oauth2", name="connector_auth_type", create_type=False),
        nullable=False,
    )

    # Fernet-encrypted JSON blob — never exposed in API responses
    encrypted_data = Column(Text, nullable=False)

    # Scopes and domain restrictions from the connector profile (S3, S8)
    scopes = Column(JSONB, nullable=False, default=list)
    allowed_domains = Column(JSONB, nullable=False, default=list)

    status = Column(
        Enum("active", "expired", "revoked", name="credential_status", create_type=False),
        nullable=False,
        default="active",
    )
    last_used_at = Column(TIMESTAMP(timezone=True), nullable=True)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)

    user = relationship("User")
