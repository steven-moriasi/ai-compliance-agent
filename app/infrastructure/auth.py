from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status


@dataclass(frozen=True)
class AuthContext:
    subject: str
    roles: frozenset[str]


def get_auth_context(
    subject: Annotated[
        str, Header(alias="X-User-ID", min_length=3, max_length=160)
    ] = "local-admin",
    roles: Annotated[str, Header(alias="X-Roles")] = "admin,analyst,reviewer,viewer",
) -> AuthContext:
    return AuthContext(
        subject=subject,
        roles=frozenset(role.strip() for role in roles.split(",") if role.strip()),
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
