from __future__ import annotations

import hmac

from backend.config import AuthSettings, get_auth_settings


def parse_bearer(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    return None


def resolve_role(token: str | None, settings: AuthSettings | None = None) -> str:
    """Map a presented token to a trusted role.

    Tokens are configured server-side (see ``get_auth_settings``); an unknown or
    missing token resolves to the lowest-privilege default role. Comparison is
    constant-time so a wrong token does not leak length/prefix timing.
    """
    settings = settings or get_auth_settings()
    if token:
        for known, role in settings.token_roles.items():
            if hmac.compare_digest(token, known):
                return role
    return settings.default_role
