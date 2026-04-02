"""Async email service using aiosmtplib.

Sends transactional emails (verification, password reset, notifications).
Falls back to logging when SMTP is not configured (development mode).
Supports runtime-configurable SMTP settings stored in the database.
"""

import html as html_mod
import logging
import time
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy import select

from app.config import settings

logger = logging.getLogger("agentnode.email")

# --- In-memory settings cache (TTL-based) ---

_smtp_cache: dict | None = None
_smtp_cache_ts: float = 0
_CACHE_TTL = 60  # seconds


async def _get_smtp_settings() -> dict:
    """Get SMTP settings from DB (cached) with env-var fallback."""
    global _smtp_cache, _smtp_cache_ts

    if _smtp_cache is not None and (time.time() - _smtp_cache_ts) < _CACHE_TTL:
        return _smtp_cache

    try:
        from app.database import async_session_factory
        from app.admin.models import SystemSetting

        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemSetting).where(SystemSetting.key == "smtp")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                _smtp_cache = row.value
                _smtp_cache_ts = time.time()
                return _smtp_cache
    except Exception as e:
        logger.debug(f"Could not load SMTP settings from DB: {e}")

    _smtp_cache = {
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "user": settings.SMTP_USER,
        "password": settings.SMTP_PASSWORD,
        "use_tls": settings.SMTP_USE_TLS,
        "from_email": settings.EMAIL_FROM,
        "from_name": settings.EMAIL_FROM_NAME,
    }
    _smtp_cache_ts = time.time()
    return _smtp_cache


def invalidate_smtp_cache() -> None:
    """Call after updating SMTP settings in the DB."""
    global _smtp_cache, _smtp_cache_ts
    _smtp_cache = None
    _smtp_cache_ts = 0


def _build_message(smtp: dict, to: str, subject: str, html_body: str, text_body: str | None = None) -> EmailMessage:
    from_name = smtp.get("from_name") or settings.EMAIL_FROM_NAME
    from_email = smtp.get("from_email") or settings.EMAIL_FROM
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body or "Please view this email in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")
    return msg


async def send_email(to: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """Send an email. Returns True on success, False on failure. Never raises."""
    smtp = await _get_smtp_settings()

    if not (smtp.get("host") and smtp.get("user") and smtp.get("password")):
        logger.warning(f"SMTP not configured — email to {to} not sent. Subject: {subject}")
        return False

    msg = _build_message(smtp, to, subject, html_body, text_body)

    try:
        port = int(smtp.get("port", 587))
        use_tls = port == 465
        start_tls = port != 465 and smtp.get("use_tls", True)

        await aiosmtplib.send(
            msg,
            hostname=smtp["host"],
            port=port,
            username=smtp["user"],
            password=smtp["password"],
            use_tls=use_tls,
            start_tls=start_tls,
            timeout=15,
        )
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False


# --- Helpers ---

# Toggleable email preferences (only recurring / potentially noisy emails)
# Security, quarantine, and one-time transactional emails always send.
EMAIL_PREF_DEFAULTS = {
    # User-toggleable
    "security_login": True,      # every login
    "package_published": True,   # every publish
    "milestone": True,           # download milestones
    "deprecated": True,          # deprecation notices
    "weekly_digest": True,       # weekly publisher digest
    # Admin-toggleable
    "admin_report_notify": True,  # every new report
    "admin_daily_digest": True,   # daily stats
}


async def check_email_pref(email: str, pref_key: str) -> bool:
    """Check if a user has the given email preference enabled.
    Returns True if the email should be sent (default), False to suppress."""
    if pref_key not in EMAIL_PREF_DEFAULTS:
        return True  # Unknown key — send by default

    try:
        from app.database import async_session_factory
        from app.auth.models import User
        async with async_session_factory() as session:
            result = await session.execute(
                select(User.email_preferences).where(User.email == email)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return EMAIL_PREF_DEFAULTS.get(pref_key, True)
            prefs = row or {}
            return prefs.get(pref_key, EMAIL_PREF_DEFAULTS.get(pref_key, True))
    except Exception:
        return True  # On error, send the email


async def get_admin_emails() -> list[str]:
    """Return email addresses of all admin users."""
    try:
        from app.database import async_session_factory
        from app.auth.models import User
        async with async_session_factory() as session:
            result = await session.execute(
                select(User.email).where(User.is_admin == True)  # noqa: E712
            )
            return [row[0] for row in result.all()]
    except Exception:
        return []


async def get_publisher_email(publisher_id) -> str | None:
    """Return the email of the user who owns a publisher."""
    try:
        from app.database import async_session_factory
        from app.auth.models import User
        from app.publishers.models import Publisher
        from sqlalchemy.orm import selectinload
        async with async_session_factory() as session:
            result = await session.execute(
                select(Publisher).options(selectinload(Publisher.user)).where(Publisher.id == publisher_id)
            )
            pub = result.scalar_one_or_none()
            return pub.user.email if pub and pub.user else None
    except Exception:
        return None


# --- Shared HTML style ---

_BASE_STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0a; color: #e5e5e5; margin: 0; padding: 0; }
  .container { max-width: 520px; margin: 40px auto; padding: 32px; background: #141414; border: 1px solid #262626; border-radius: 12px; }
  .logo { font-size: 20px; font-weight: 700; color: #ffffff; margin-bottom: 24px; }
  .logo span { color: #6366f1; }
  h1 { font-size: 18px; color: #ffffff; margin: 0 0 12px 0; }
  p { font-size: 14px; line-height: 1.6; color: #a3a3a3; margin: 0 0 16px 0; }
  .btn { display: inline-block; padding: 12px 28px; background: #6366f1; color: #ffffff !important; text-decoration: none; border-radius: 8px; font-size: 14px; font-weight: 600; }
  .code { font-family: monospace; font-size: 13px; background: #1e1e1e; border: 1px solid #333; padding: 12px 16px; border-radius: 6px; word-break: break-all; color: #d4d4d4; }
  .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #262626; font-size: 12px; color: #666; }
  .warn { color: #f59e0b; font-weight: 600; }
  .success { color: #22c55e; font-weight: 600; }
</style>
"""

_LOGO = '<div class="logo">Agent<span>Node</span></div>'
_FOOTER_AUTOMATED = '<div class="footer"><p>This is an automated notification from AgentNode.</p></div>'


def _esc(value: str) -> str:
    """Escape HTML special characters in user-provided values."""
    return html_mod.escape(str(value))


def _wrap(body_content: str) -> str:
    return f'<!DOCTYPE html><html><head>{_BASE_STYLE}</head><body><div class="container">{_LOGO}{body_content}{_FOOTER_AUTOMATED}</div></body></html>'


# =========================================================================
#  PHASE 1 — Security & Core
# =========================================================================


# 1. Welcome email (with embedded verification link)
async def send_welcome_email(to: str, username: str, verify_token: str) -> bool:
    verify_url = f"{settings.FRONTEND_URL}/auth/verify-email?token={verify_token}"
    html = _wrap(f"""
      <h1>Welcome to AgentNode, {_esc(username)}!</h1>
      <p>Your account has been created. AgentNode is the open registry where AI agents discover, install, and use capabilities across every framework.</p>
      <p>First, verify your email address:</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{verify_url}" class="btn">Verify Email</a>
      </p>
      <p>Or copy this link:</p>
      <div class="code">{verify_url}</div>
      <div class="footer"><p>This link expires in 24 hours.</p></div>
    """)
    return await send_email(to, "Welcome to AgentNode!", html,
        f"Welcome to AgentNode, {username}! Verify your email: {verify_url}")


# 2. Email verification (standalone, for re-requests)
async def send_verification_email(to: str, token: str) -> bool:
    verify_url = f"{settings.FRONTEND_URL}/auth/verify-email?token={token}"
    html = _wrap(f"""
      <h1>Verify your email</h1>
      <p>Click the button below to verify your email address.</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{verify_url}" class="btn">Verify Email</a>
      </p>
      <p>Or copy this link:</p>
      <div class="code">{verify_url}</div>
      <div class="footer"><p>This link expires in 24 hours. If you didn't request this, you can safely ignore it.</p></div>
    """)
    return await send_email(to, "Verify your email - AgentNode", html,
        f"Verify your AgentNode email: {verify_url}")


# 3. Password changed notification
async def send_password_changed_email(to: str) -> bool:
    reset_url = f"{settings.FRONTEND_URL}/auth/forgot-password"
    html = _wrap(f"""
      <h1>Password changed</h1>
      <p>Your AgentNode password was changed successfully.</p>
      <p class="warn">If you did not make this change, reset your password immediately:</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{reset_url}" class="btn">Reset Password</a>
      </p>
    """)
    return await send_email(to, "Password changed - AgentNode", html,
        f"Your AgentNode password was changed. If this wasn't you, reset it: {reset_url}")


# 4. Password reset confirmation
async def send_password_reset_email(to: str, token: str) -> bool:
    reset_url = f"{settings.FRONTEND_URL}/auth/reset-password?token={token}"
    html = _wrap(f"""
      <h1>Reset your password</h1>
      <p>We received a request to reset your password. Click the button below to choose a new one.</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{reset_url}" class="btn">Reset Password</a>
      </p>
      <p>Or copy this link:</p>
      <div class="code">{reset_url}</div>
      <div class="footer"><p>This link expires in 1 hour. If you didn't request this, ignore this email.</p></div>
    """)
    return await send_email(to, "Reset your password - AgentNode", html,
        f"Reset your AgentNode password: {reset_url}")


# 5. Password reset completed
async def send_password_reset_confirmation_email(to: str) -> bool:
    html = _wrap("""
      <h1>Password reset successful</h1>
      <p class="success">Your password has been reset successfully.</p>
      <p>You can now sign in with your new password.</p>
      <p class="warn">If you did not reset your password, contact support immediately.</p>
    """)
    return await send_email(to, "Password reset complete - AgentNode", html,
        "Your AgentNode password has been reset. If this wasn't you, contact support.")


# 6. Email changed — send verification to NEW address
async def send_email_changed_verify(new_email: str, token: str) -> bool:
    verify_url = f"{settings.FRONTEND_URL}/auth/verify-email?token={token}"
    html = _wrap(f"""
      <h1>Verify your new email</h1>
      <p>Your AgentNode account email was changed to this address. Please verify it:</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{verify_url}" class="btn">Verify Email</a>
      </p>
      <p>Or copy this link:</p>
      <div class="code">{verify_url}</div>
      <div class="footer"><p>This link expires in 24 hours.</p></div>
    """)
    return await send_email(new_email, "Verify your new email - AgentNode", html,
        f"Verify your new email: {verify_url}")


# 7. Email changed — alert to OLD address
async def send_email_changed_alert(old_email: str, new_email: str) -> bool:
    html = _wrap(f"""
      <h1>Email address changed</h1>
      <p>The email address on your AgentNode account was changed to <strong>{_esc(new_email)}</strong>.</p>
      <p class="warn">If you did not make this change, your account may be compromised. Contact support immediately.</p>
    """)
    return await send_email(old_email, "Email address changed - AgentNode", html,
        f"Your AgentNode email was changed to {new_email}. If this wasn't you, contact support.")


# 8. 2FA enabled
async def send_2fa_enabled_email(to: str) -> bool:
    html = _wrap("""
      <h1>Two-factor authentication enabled</h1>
      <p class="success">2FA has been enabled on your AgentNode account.</p>
      <p>You will need your authenticator app for future logins. Keep your backup codes safe.</p>
      <p class="warn">If you did not enable 2FA, secure your account immediately.</p>
    """)
    return await send_email(to, "2FA enabled - AgentNode", html,
        "Two-factor authentication has been enabled on your AgentNode account.")


# 9. Publisher suspended (existing, refactored)
async def send_publisher_suspended_email(to: str, publisher_slug: str, reason: str) -> bool:
    html = _wrap(f"""
      <h1>Publisher account suspended</h1>
      <p>Your publisher account <strong>@{_esc(publisher_slug)}</strong> has been suspended by an administrator.</p>
      <p><strong>Reason:</strong> {_esc(reason)}</p>
      <p>If you believe this was done in error, please contact support.</p>
    """)
    return await send_email(to, f"Publisher @{publisher_slug} suspended - AgentNode", html)


# 10. Publisher unsuspended
async def send_publisher_unsuspended_email(to: str, publisher_slug: str) -> bool:
    html = _wrap(f"""
      <h1>Publisher account reinstated</h1>
      <p class="success">Your publisher account <strong>@{publisher_slug}</strong> has been reinstated.</p>
      <p>You can resume publishing packages on AgentNode.</p>
    """)
    return await send_email(to, f"Publisher @{publisher_slug} reinstated - AgentNode", html)


# 11. Package quarantined (existing, refactored)
async def send_quarantine_email(to: str, package_slug: str, version: str, reason: str) -> bool:
    html = _wrap(f"""
      <h1>Package version quarantined</h1>
      <p>Version <strong>{_esc(version)}</strong> of <strong>{_esc(package_slug)}</strong> has been quarantined.</p>
      <p><strong>Reason:</strong> {_esc(reason)}</p>
      <p>The version is temporarily hidden from search and installation. You may be contacted for further details.</p>
    """)
    return await send_email(to, f"{package_slug}@{version} quarantined - AgentNode", html)


# 12. Quarantine cleared
async def send_quarantine_cleared_email(to: str, package_slug: str, version: str) -> bool:
    html = _wrap(f"""
      <h1>Quarantine cleared</h1>
      <p class="success">Version <strong>{version}</strong> of <strong>{package_slug}</strong> has been cleared.</p>
      <p>The version is now publicly available for search and installation.</p>
    """)
    return await send_email(to, f"{package_slug}@{version} cleared - AgentNode", html)


# 13. Version rejected
async def send_version_rejected_email(to: str, package_slug: str, version: str) -> bool:
    html = _wrap(f"""
      <h1>Package version rejected</h1>
      <p>Version <strong>{version}</strong> of <strong>{package_slug}</strong> has been permanently rejected by an administrator.</p>
      <p>This version will not be available for installation. If you believe this is an error, please contact support.</p>
    """)
    return await send_email(to, f"{package_slug}@{version} rejected - AgentNode", html)


# 14. Report submitted — admin notification
async def send_report_admin_notification(package_slug: str, reason: str, reporter_username: str) -> None:
    """Send notification to all admins about a new report."""
    admin_emails = [e for e in await get_admin_emails() if await check_email_pref(e, "admin_report_notify")]
    admin_url = f"{settings.FRONTEND_URL}/admin/reports"
    html = _wrap(f"""
      <h1>New package report</h1>
      <p>A report has been filed against <strong>{_esc(package_slug)}</strong> by @{_esc(reporter_username)}.</p>
      <p><strong>Reason:</strong> {_esc(reason)}</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{admin_url}" class="btn">Review Reports</a>
      </p>
    """)
    for email in admin_emails:
        await send_email(email, f"New report: {package_slug} - AgentNode", html)


# =========================================================================
#  PHASE 2 — Professional
# =========================================================================


# 15. Package published confirmation
async def send_package_published_email(to: str, package_slug: str, version: str, quarantined: bool = False) -> bool:
    if not await check_email_pref(to, "package_published"):
        return True
    quarantine_note = ""
    if quarantined:
        quarantine_note = '<p class="warn">Note: This version has been auto-quarantined for review. An administrator will review it shortly.</p>'

    html = _wrap(f"""
      <h1>Package published</h1>
      <p class="success">You successfully published <strong>{package_slug}@{version}</strong>.</p>
      {quarantine_note}
      <p>Your package is now available on AgentNode.</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/packages/{package_slug}" class="btn">View Package</a>
      </p>
    """)
    return await send_email(to, f"Published {package_slug}@{version} - AgentNode", html,
        f"You published {package_slug}@{version} on AgentNode.")


# 16. Auto-quarantine notification (security scanner)
async def send_auto_quarantine_email(to: str, package_slug: str, version: str, finding_count: int) -> bool:
    html = _wrap(f"""
      <h1>Version auto-quarantined</h1>
      <p>Version <strong>{version}</strong> of <strong>{package_slug}</strong> was automatically quarantined by our security scanner.</p>
      <p><strong>{finding_count} high-severity finding(s)</strong> were detected.</p>
      <p>An administrator will review your package. You may be contacted for further details.</p>
      <p class="warn">Common reasons: embedded secrets, undeclared code execution, undeclared network access.</p>
    """)
    return await send_email(to, f"{package_slug}@{version} auto-quarantined - AgentNode", html)


# 17. Publisher created confirmation
async def send_publisher_created_email(to: str, publisher_slug: str) -> bool:
    html = _wrap(f"""
      <h1>Publisher profile created</h1>
      <p class="success">Your publisher profile <strong>@{publisher_slug}</strong> has been created.</p>
      <p>You can now publish packages on AgentNode. Get started:</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/publish" class="btn">Publish a Package</a>
      </p>
    """)
    return await send_email(to, f"Publisher @{publisher_slug} created - AgentNode", html,
        f"Your publisher @{publisher_slug} is ready. Start publishing on AgentNode!")


# 18. Trust level changed
async def send_trust_level_changed_email(to: str, publisher_slug: str, old_level: str, new_level: str) -> bool:
    html = _wrap(f"""
      <h1>Trust level updated</h1>
      <p>The trust level for <strong>@{publisher_slug}</strong> has been changed from
      <strong>{old_level}</strong> to <strong>{new_level}</strong>.</p>
      <p>Trust levels affect how your packages appear in search results and whether new versions require review.</p>
    """)
    return await send_email(to, f"Trust level changed: @{publisher_slug} - AgentNode", html)


# 19. Report resolved — notify reporter
async def send_report_resolved_reporter_email(to: str, package_slug: str, status: str, resolution_note: str | None) -> bool:
    note_html = f"<p><strong>Note:</strong> {_esc(resolution_note)}</p>" if resolution_note else ""
    html = _wrap(f"""
      <h1>Report update</h1>
      <p>Your report against <strong>{package_slug}</strong> has been <strong>{status}</strong> by an administrator.</p>
      {note_html}
      <p>Thank you for helping keep AgentNode safe.</p>
    """)
    return await send_email(to, f"Report {status}: {package_slug} - AgentNode", html)


# 20. Login from new IP
async def send_new_login_alert_email(to: str, ip_address: str, user_agent: str) -> bool:
    if not await check_email_pref(to, "security_login"):
        return True
    reset_url = f"{settings.FRONTEND_URL}/auth/forgot-password"
    html = _wrap(f"""
      <h1>New sign-in detected</h1>
      <p>A new sign-in to your AgentNode account was detected.</p>
      <p><strong>IP:</strong> {_esc(ip_address)}<br><strong>Device:</strong> {_esc(user_agent[:100])}</p>
      <p class="warn">If this wasn't you, reset your password immediately:</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{reset_url}" class="btn">Reset Password</a>
      </p>
    """)
    return await send_email(to, "New sign-in to your account - AgentNode", html,
        f"New sign-in to your AgentNode account from {ip_address}. Reset password if unauthorized: {reset_url}")


# 21. API key created
async def send_api_key_created_email(to: str, label: str | None, key_prefix: str) -> bool:
    html = _wrap(f"""
      <h1>API key created</h1>
      <p>A new API key was created for your AgentNode account.</p>
      <p><strong>Label:</strong> {_esc(label) if label else '(none)'}<br><strong>Prefix:</strong> {_esc(key_prefix)}...</p>
      <p class="warn">If you did not create this key, revoke it immediately in your dashboard.</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/dashboard" class="btn">Go to Dashboard</a>
      </p>
    """)
    return await send_email(to, "New API key created - AgentNode", html,
        f"A new API key ({key_prefix}...) was created for your account.")


# =========================================================================
#  PHASE 3 — Engagement
# =========================================================================


# 22. Download milestone
async def send_download_milestone_email(to: str, package_slug: str, milestone: int) -> bool:
    if not await check_email_pref(to, "milestone"):
        return True
    html = _wrap(f"""
      <h1>Congratulations!</h1>
      <p class="success"><strong>{package_slug}</strong> just reached <strong>{milestone:,}</strong> downloads!</p>
      <p>Your work is helping developers build better AI agents. Keep it up!</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/packages/{package_slug}" class="btn">View Package</a>
      </p>
    """)
    return await send_email(to, f"{package_slug} reached {milestone:,} downloads! - AgentNode", html)


# 23. Package deprecated notification (to users with active installs)
async def send_package_deprecated_email(to: str, package_slug: str) -> bool:
    if not await check_email_pref(to, "deprecated"):
        return True
    html = _wrap(f"""
      <h1>Package deprecated</h1>
      <p>The package <strong>{package_slug}</strong> has been marked as deprecated by its publisher.</p>
      <p>It will remain available but is no longer actively maintained. Consider migrating to an alternative.</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/search" class="btn">Find Alternatives</a>
      </p>
    """)
    return await send_email(to, f"{package_slug} deprecated - AgentNode", html)


# 24. Security scan report
async def send_security_scan_report_email(to: str, package_slug: str, version: str, finding_count: int, high_count: int) -> bool:
    severity_note = f'<p class="warn">{high_count} high-severity finding(s) detected.</p>' if high_count else ""
    html = _wrap(f"""
      <h1>Security scan results</h1>
      <p>The security scan for <strong>{package_slug}@{version}</strong> has completed.</p>
      <p><strong>{finding_count} finding(s)</strong> were detected.</p>
      {severity_note}
      <p>Review details in your dashboard or contact support for guidance.</p>
    """)
    return await send_email(to, f"Scan results: {package_slug}@{version} - AgentNode", html)


# 25. Weekly publisher digest
async def send_weekly_publisher_digest(to: str, publisher_slug: str, stats: dict) -> bool:
    if not await check_email_pref(to, "weekly_digest"):
        return True
    html = _wrap(f"""
      <h1>Weekly digest for @{publisher_slug}</h1>
      <p>Here's your weekly summary:</p>
      <table style="width:100%; font-size:14px; color:#a3a3a3;">
        <tr><td>Downloads this week</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('downloads', 0):,}</td></tr>
        <tr><td>New installations</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('installs', 0):,}</td></tr>
        <tr><td>Total packages</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('packages', 0)}</td></tr>
      </table>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/dashboard" class="btn">View Dashboard</a>
      </p>
    """)
    return await send_email(to, f"Weekly digest: @{publisher_slug} - AgentNode", html)


# 26. Daily admin digest
async def send_admin_daily_digest(to: str, stats: dict) -> bool:
    if not await check_email_pref(to, "admin_daily_digest"):
        return True
    html = _wrap(f"""
      <h1>Daily admin digest</h1>
      <table style="width:100%; font-size:14px; color:#a3a3a3;">
        <tr><td>New users (24h)</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('new_users', 0)}</td></tr>
        <tr><td>New packages (24h)</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('new_packages', 0)}</td></tr>
        <tr><td>Open reports</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('open_reports', 0)}</td></tr>
        <tr><td>Quarantined versions</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('quarantined', 0)}</td></tr>
        <tr><td>Total downloads (24h)</td><td style="text-align:right; color:#fff; font-weight:600;">{stats.get('downloads_24h', 0):,}</td></tr>
      </table>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/admin" class="btn">Open Admin Panel</a>
      </p>
    """)
    return await send_email(to, "Daily digest - AgentNode Admin", html)


# =========================================================================
#  PHASE 4 — Creator Outreach
# =========================================================================


# 27. Invite outreach email (sent to tool creators)
async def send_invite_outreach_email(
    to: str,
    contact_name: str | None,
    display_name: str,
    description: str | None,
    source_url: str | None,
    tracking_url: str,
) -> bool:
    greeting = f"Hi {contact_name}," if contact_name else "Hi,"

    html = _wrap(f"""
      <h1>Get {display_name} in front of every AI agent</h1>
      <p>{greeting}</p>
      <p>I&rsquo;m reaching out because <strong style="color:#ffffff;">{display_name}</strong> looks like a great fit for
      <a href="https://agentnode.net" style="color:#818cf8;">AgentNode</a> &mdash; the verified registry where AI agents
      automatically discover, install, and use tools at runtime.</p>

      <p style="font-size:14px; font-weight:600; color:#ffffff; margin-top:20px;">What that means for you:</p>
      <table style="width:100%; font-size:13px; color:#a3a3a3; border-collapse:collapse;">
        <tr>
          <td style="padding:8px 0; vertical-align:top; width:24px; color:#6366f1;">&bull;</td>
          <td style="padding:8px 0;"><strong style="color:#e5e5e5;">Auto-discovery by agents</strong> &mdash; When an agent needs a capability your tool provides, it finds and installs it automatically. No marketing required.</td>
        </tr>
        <tr>
          <td style="padding:8px 0; vertical-align:top; color:#6366f1;">&bull;</td>
          <td style="padding:8px 0;"><strong style="color:#e5e5e5;">Works across frameworks</strong> &mdash; One listing works with LangChain, CrewAI, MCP, and plain Python. No separate integrations.</td>
        </tr>
        <tr>
          <td style="padding:8px 0; vertical-align:top; color:#6366f1;">&bull;</td>
          <td style="padding:8px 0;"><strong style="color:#e5e5e5;">Verified trust badge</strong> &mdash; Every package is sandbox-tested on publish. Your quality is proven, not self-reported. Agents trust verified tools by default.</td>
        </tr>
        <tr>
          <td style="padding:8px 0; vertical-align:top; color:#6366f1;">&bull;</td>
          <td style="padding:8px 0;"><strong style="color:#e5e5e5;">Real usage analytics</strong> &mdash; See exactly how many agents install and use your tool, across which frameworks.</td>
        </tr>
      </table>

      <p style="margin-top:16px;">We&rsquo;ve already pre-filled your tool&rsquo;s metadata from the repo. Publishing takes about 2 minutes &mdash; just review, adjust anything you like, and publish under your own name.</p>

      <p style="text-align:center; margin: 28px 0;">
        <a href="{tracking_url}" class="btn">Publish {display_name} &rarr;</a>
      </p>
      <p style="font-size:13px; color:#737373;">Nothing is published automatically. You have full control over the listing.</p>
      <div class="footer">
        <p>You received this because you maintain an open-source tool that fits AgentNode&rsquo;s registry.<br>
        Not interested? Simply ignore this email.</p>
      </div>
    """)

    text = (
        f"{greeting}\n\n"
        f"I'm reaching out because {display_name} looks like a great fit for AgentNode — "
        f"the verified registry where AI agents automatically discover, install, and use tools at runtime.\n\n"
        f"What that means for you:\n"
        f"- Auto-discovery: agents find and install your tool when they need it\n"
        f"- Cross-framework: one listing works with LangChain, CrewAI, MCP, and Python\n"
        f"- Verified badge: sandbox-tested, agents trust you by default\n"
        f"- Usage analytics: see how many agents use your tool\n\n"
        f"We pre-filled your metadata. Publishing takes ~2 minutes:\n"
        f"{tracking_url}\n\n"
        f"Nothing is published automatically. You have full control.\n"
    )

    return await send_email(
        to,
        f"{display_name}: get discovered by AI agents",
        html,
        text,
    )


# 28. Follow-up email (for creators who were contacted but didn't click)
async def send_invite_followup_email(
    to: str,
    contact_name: str | None,
    display_name: str,
    tracking_url: str,
) -> bool:
    greeting = f"Hi {contact_name}," if contact_name else "Hi,"

    html = _wrap(f"""
      <h1>Quick follow-up on {display_name}</h1>
      <p>{greeting}</p>
      <p>Last week I reached out about listing <strong style="color:#ffffff;">{display_name}</strong> on AgentNode.</p>
      <p>The short version: AI agents are increasingly searching for tools at runtime &mdash; not on PyPI or npm, but through capability registries like AgentNode. When they need what your tool does, they find it, install it, and use it automatically.</p>
      <p>We&rsquo;ve already pre-filled your listing from the repo. It takes about 2 minutes to review and publish:</p>
      <p style="text-align:center; margin: 28px 0;">
        <a href="{tracking_url}" class="btn">Review your listing &rarr;</a>
      </p>
      <p style="font-size:13px; color:#737373;">Questions? Just reply to this email. Not interested? No worries &mdash; this is the last follow-up.</p>
    """)

    text = (
        f"{greeting}\n\n"
        f"Last week I reached out about listing {display_name} on AgentNode.\n\n"
        f"AI agents search for tools at runtime through capability registries. "
        f"When they need what your tool does, they find it, install it, and use it automatically.\n\n"
        f"Review your pre-filled listing (~2 min): {tracking_url}\n\n"
        f"Questions? Reply to this email. Not interested? This is the last follow-up.\n"
    )

    return await send_email(
        to,
        f"Quick follow-up: {display_name} on AgentNode",
        html,
        text,
    )


# =========================================================================
#  PHASE 5 — Review Notifications (transactional, always send)
# =========================================================================


# 29. Review payment received
async def send_review_payment_received_email(
    to: str, package_slug: str, version: str, tier: str, express: bool, price_cents: int,
) -> bool:
    tier_labels = {"security": "Security Review", "compatibility": "Compatibility Review", "full": "Full Review"}
    tier_label = tier_labels.get(tier, tier.title())
    turnaround = "48 hours (Express)" if express else "7 business days"
    price_str = f"${price_cents / 100:.0f}"

    html = _wrap(f"""
      <h1>Payment received</h1>
      <p class="success">Your review request has been confirmed.</p>
      <table style="width:100%; font-size:14px; color:#a3a3a3; margin:16px 0;">
        <tr><td>Package</td><td style="text-align:right; color:#fff; font-weight:600;">{package_slug}@{version}</td></tr>
        <tr><td>Tier</td><td style="text-align:right; color:#fff; font-weight:600;">{tier_label}</td></tr>
        <tr><td>Turnaround</td><td style="text-align:right; color:#fff; font-weight:600;">{turnaround}</td></tr>
        <tr><td>Amount paid</td><td style="text-align:right; color:#fff; font-weight:600;">{price_str} USD</td></tr>
      </table>
      <p>We&rsquo;ll notify you when the review is complete. Track status in your dashboard:</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/dashboard" class="btn">View Dashboard</a>
      </p>
    """)
    return await send_email(to, f"Payment received: {package_slug} review - AgentNode", html,
        f"Payment confirmed for {tier_label} of {package_slug}@{version}. {price_str} USD. Turnaround: {turnaround}.")


# 30. Review completed
async def send_review_completed_email(
    to: str, package_slug: str, version: str, tier: str,
    outcome: str, review_result: dict | None, notes: str | None,
) -> bool:
    outcome_labels = {
        "approved": ('<span class="success">Approved</span>', "#22c55e"),
        "changes_requested": ('<span style="color:#f59e0b; font-weight:600;">Changes Requested</span>', "#f59e0b"),
        "rejected": ('<span style="color:#ef4444; font-weight:600;">Rejected</span>', "#ef4444"),
    }
    badge_html, _ = outcome_labels.get(outcome, (outcome, "#a3a3a3"))

    checks_html = ""
    if review_result:
        checks = []
        if "security_passed" in review_result:
            icon = '<span style="color:#22c55e;">&#10003;</span>' if review_result["security_passed"] else '<span style="color:#ef4444;">&#10007;</span>'
            checks.append(f"{icon} Security")
        if "compatibility_passed" in review_result:
            icon = '<span style="color:#22c55e;">&#10003;</span>' if review_result["compatibility_passed"] else '<span style="color:#ef4444;">&#10007;</span>'
            checks.append(f"{icon} Compatibility")
        if "docs_passed" in review_result:
            icon = '<span style="color:#22c55e;">&#10003;</span>' if review_result["docs_passed"] else '<span style="color:#ef4444;">&#10007;</span>'
            checks.append(f"{icon} Documentation")
        if checks:
            checks_html = '<p style="font-size:14px;">' + " &nbsp;&middot;&nbsp; ".join(checks) + '</p>'

    changes_html = ""
    required_changes = review_result.get("required_changes", []) if review_result else []
    if required_changes:
        items = "".join(f"<li style='margin:4px 0;'>{_esc(c)}</li>" for c in required_changes)
        changes_html = f'<div style="margin:12px 0;"><p style="color:#f59e0b; font-weight:600; font-size:14px;">Required changes:</p><ul style="color:#a3a3a3; font-size:13px; padding-left:20px;">{items}</ul></div>'

    summary_html = ""
    if review_result and review_result.get("reviewer_summary"):
        summary_html = f'<p style="font-size:13px; color:#a3a3a3; margin:12px 0; padding:12px; background:#1e1e1e; border-radius:6px; border:1px solid #333;">{review_result["reviewer_summary"]}</p>'

    notes_html = ""
    if notes:
        notes_html = f'<p style="font-size:13px; color:#a3a3a3; font-style:italic;">{_esc(notes)}</p>'

    next_steps_html = ""
    if outcome == "changes_requested":
        next_steps_html = '<p style="color:#f59e0b; font-size:13px; margin-top:16px;">Fix the issues listed above, publish a new version, then request another review for the new version.</p>'

    html = _wrap(f"""
      <h1>Review complete: {package_slug}@{version}</h1>
      <p>Outcome: {badge_html}</p>
      {checks_html}
      {changes_html}
      {summary_html}
      {notes_html}
      {next_steps_html}
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/dashboard" class="btn">View Details</a>
      </p>
    """)
    return await send_email(to, f"Review {outcome}: {package_slug}@{version} - AgentNode", html,
        f"Your {tier} review for {package_slug}@{version} is complete. Outcome: {outcome}.")


# 31. Review refund
async def send_review_refund_email(
    to: str, package_slug: str, version: str, refund_amount_cents: int, is_full: bool,
) -> bool:
    refund_str = f"${refund_amount_cents / 100:.0f}"
    refund_type = "Full refund" if is_full else "Partial refund"
    badge_note = "<p class='warn'>The review badge has been removed from this version.</p>" if is_full else ""

    html = _wrap(f"""
      <h1>{refund_type} processed</h1>
      <p>A refund has been issued for your review of <strong>{package_slug}@{version}</strong>.</p>
      <table style="width:100%; font-size:14px; color:#a3a3a3; margin:16px 0;">
        <tr><td>Refund amount</td><td style="text-align:right; color:#fff; font-weight:600;">{refund_str} USD</td></tr>
        <tr><td>Type</td><td style="text-align:right; color:#fff; font-weight:600;">{refund_type}</td></tr>
      </table>
      {badge_note}
      <p>The refund will appear on your statement within 5-10 business days.</p>
      <p style="text-align:center; margin: 24px 0;">
        <a href="{settings.FRONTEND_URL}/dashboard" class="btn">View Dashboard</a>
      </p>
    """)
    return await send_email(to, f"Refund: {package_slug} review - AgentNode", html,
        f"{refund_type} of {refund_str} USD for {package_slug}@{version} review.")
