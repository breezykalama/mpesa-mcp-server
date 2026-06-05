"""Tests for OIDC group-to-role mapping."""

from __future__ import annotations

from app.auth.role_mapping import OIDCRoleMapper
from app.config import Settings


def build_mapper() -> OIDCRoleMapper:
    return OIDCRoleMapper.from_settings(
        Settings(
            operator_auth_enabled=False,
            oidc_viewer_groups="finance_viewers,ops_viewers",
            oidc_approver_groups="finance_approvers",
            oidc_admin_groups="finance_admins",
        )
    )


def test_viewer_group_mapping() -> None:
    result = build_mapper().map_groups(["finance_viewers"])

    assert result.roles == ["viewer"]
    assert result.matched_groups == ["finance_viewers"]


def test_approver_group_mapping_inherits_viewer() -> None:
    result = build_mapper().map_groups(["finance_approvers"])

    assert result.roles == ["viewer", "approver"]


def test_admin_group_mapping_inherits_all_roles() -> None:
    result = build_mapper().map_groups(["finance_admins"])

    assert result.roles == ["viewer", "approver", "admin"]


def test_multi_group_mapping_deduplicates_roles() -> None:
    result = build_mapper().map_groups(["finance_viewers", "finance_approvers"])

    assert result.roles == ["viewer", "approver"]
    assert result.matched_groups == ["finance_viewers", "finance_approvers"]


def test_unknown_groups_do_not_grant_roles() -> None:
    result = build_mapper().map_groups(["unknown_group"])

    assert result.roles == []
    assert result.matched_groups == []
    assert result.unknown_groups == ["unknown_group"]


def test_empty_groups_do_not_grant_roles() -> None:
    result = build_mapper().map_groups([])

    assert result.roles == []
    assert result.matched_groups == []
    assert result.unknown_groups == []
