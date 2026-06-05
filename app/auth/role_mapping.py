"""OIDC group-to-platform-role mapping."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, Field

from app.config import Settings

OperatorRole = Literal["viewer", "approver", "admin"]
ROLE_ORDER = ("viewer", "approver", "admin")
ROLE_INHERITANCE: dict[OperatorRole, tuple[OperatorRole, ...]] = {
    "viewer": ("viewer",),
    "approver": ("viewer", "approver"),
    "admin": ("viewer", "approver", "admin"),
}


class RoleMappingResult(BaseModel):
    """Result of mapping OIDC groups into platform roles."""

    roles: list[OperatorRole] = Field(default_factory=list)
    matched_groups: list[str] = Field(default_factory=list)
    unknown_groups: list[str] = Field(default_factory=list)


class OIDCRoleMapper:
    """Map OIDC group claims into platform RBAC roles."""

    def __init__(
        self,
        *,
        viewer_groups: set[str],
        approver_groups: set[str],
        admin_groups: set[str],
    ) -> None:
        self._group_role_map: dict[str, OperatorRole] = {}
        self._add_group_mappings(viewer_groups, "viewer")
        self._add_group_mappings(approver_groups, "approver")
        self._add_group_mappings(admin_groups, "admin")

    @classmethod
    def from_settings(cls, settings: Settings) -> OIDCRoleMapper:
        """Build a mapper from comma-separated OIDC group settings."""

        return cls(
            viewer_groups=_parse_csv(settings.oidc_viewer_groups),
            approver_groups=_parse_csv(settings.oidc_approver_groups),
            admin_groups=_parse_csv(settings.oidc_admin_groups),
        )

    def map_groups(self, groups: list[str]) -> RoleMappingResult:
        """Map OIDC groups to inherited, deduplicated platform roles."""

        matched_groups: list[str] = []
        unknown_groups: list[str] = []
        mapped_roles: set[str] = set()

        for group in _normalize_groups(groups):
            role = self._group_role_map.get(group)
            if role is None:
                unknown_groups.append(group)
                continue

            matched_groups.append(group)
            mapped_roles.update(ROLE_INHERITANCE[role])

        return RoleMappingResult(
            roles=cast(
                list[OperatorRole],
                [role for role in ROLE_ORDER if role in mapped_roles],
            ),
            matched_groups=list(dict.fromkeys(matched_groups)),
            unknown_groups=list(dict.fromkeys(unknown_groups)),
        )

    def _add_group_mappings(self, groups: set[str], role: OperatorRole) -> None:
        for group in groups:
            self._group_role_map[group] = role


def _parse_csv(value: str) -> set[str]:
    return set(_normalize_groups(value.split(",")))


def _normalize_groups(groups: list[str]) -> list[str]:
    return [group.strip() for group in groups if group.strip()]
