from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class AuthContext:
    subject: str
    roles: frozenset[str]


class TokenClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: str = Field(min_length=3, max_length=160)
    roles: list[str] = Field(default_factory=list)
    scope: str = ""


@lru_cache
def get_jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True)


def decode_oidc_token(token: str, settings: Settings) -> TokenClaims:
    if not settings.oidc_issuer or not settings.oidc_audience or not settings.oidc_jwks_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC authentication is not fully configured",
        )
    try:
        signing_key = get_jwk_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        decoded: object = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
        )
        return TokenClaims.model_validate(decoded)
    except (PyJWKClientError, PyJWTError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_auth_context(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    subject: Annotated[str | None, Header(alias="X-User-ID")] = None,
    roles: Annotated[str | None, Header(alias="X-Roles")] = None,
) -> AuthContext:
    if settings.auth_mode == "oidc":
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        claims = decode_oidc_token(authorization.removeprefix("Bearer ").strip(), settings)
        token_roles = set(claims.roles)
        token_roles.update(claims.scope.split())
        return AuthContext(subject=claims.sub, roles=frozenset(token_roles))

    if settings.environment != "development":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Development authentication is disabled outside development",
        )
    return AuthContext(
        subject=subject or "local-admin",
        roles=frozenset(
            role.strip()
            for role in (roles or "admin,analyst,reviewer,viewer").split(",")
            if role.strip()
        ),
    )


def require_roles(*allowed_roles: str) -> Callable[[AuthContext], AuthContext]:
    def authorize(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
        if context.roles.isdisjoint(allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Required role is missing",
            )
        return context

    return authorize
