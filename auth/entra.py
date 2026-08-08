import logging
import os
import time

import jwt
import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_TENANT_ID = os.environ["AZURE_TENANT_ID"]
_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]

_JWKS_URL = f"https://login.microsoftonline.com/{_TENANT_ID}/discovery/v2.0/keys"
_ISSUER = f"https://sts.windows.net/{_TENANT_ID}/"
_AUDIENCE = f"api://{_CLIENT_ID}"
_JWKS_CACHE_TTL_SECONDS = 3600

_bearer_scheme = HTTPBearer(auto_error=True)

_jwks_cache: dict = {"keys_by_kid": {}, "fetched_at": 0.0}


def _refresh_jwks() -> None:
    response = requests.get(_JWKS_URL, timeout=5)
    response.raise_for_status()
    keys = response.json()["keys"]
    _jwks_cache["keys_by_kid"] = {key["kid"]: jwt.PyJWK.from_dict(key).key for key in keys}
    _jwks_cache["fetched_at"] = time.monotonic()


def _get_signing_key(kid: str):
    stale = (time.monotonic() - _jwks_cache["fetched_at"]) > _JWKS_CACHE_TTL_SECONDS
    if stale or kid not in _jwks_cache["keys_by_kid"]:
        # Also covers Entra's periodic key rotation: an unrecognized kid
        # triggers one refresh before we give up on the token.
        _refresh_jwks()

    key = _jwks_cache["keys_by_kid"].get(kid)
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unable to find matching signing key for token")
    return key


def _decode(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token") from exc

    signing_key = _get_signing_key(header["kid"])

    try:
        return jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            audience=_AUDIENCE,
            issuer=_ISSUER,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc


def require_role(role: str):
    """FastAPI dependency factory gating a route to callers whose token carries `role`.

    Verifies signature (against Entra's cached JWKS), audience, issuer, and
    expiry, then checks the decoded `roles` claim. Returns the decoded claims
    so the route can attribute the action to a specific caller.
    """
    def dependency(
        credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    ) -> dict:
        claims = _decode(credentials.credentials)
        if role not in claims.get("roles", []):
            logger.warning("Access denied: subject %s missing role %s", claims.get("oid"), role)
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing required role: {role}")
        return claims

    return dependency
