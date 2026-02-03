from __future__ import annotations

import sys
import unicodedata
from typing import Iterable

import requests

from app.config import get_settings


def _normalize_slug(value: str) -> str:
    normalized = value.replace("_", " ").replace("-", " ")
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(normalized.lower().split())


def _graphql(token: str, query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(payload["errors"])
    return payload.get("data", {})


def _get_owner_logins(token: str) -> list[str]:
    query = """
    query {
      viewer {
        login
        organizations(first: 100) { nodes { login } }
      }
    }
    """
    data = _graphql(token, query)
    viewer = data.get("viewer") or {}
    logins = []
    if viewer.get("login"):
        logins.append(viewer.get("login"))
    orgs = (viewer.get("organizations") or {}).get("nodes") or []
    for org in orgs:
        login = org.get("login")
        if login:
            logins.append(login)
    return logins


def _find_project_id_for_owner(token: str, owner: str, slug: str) -> str | None:
    query = """
    query($login: String!) {
      user(login: $login) {
        projectsV2(first: 100) { nodes { id title } }
      }
      organization(login: $login) {
        projectsV2(first: 100) { nodes { id title } }
      }
    }
    """
    data = _graphql(token, query, {"login": owner})
    candidates = []
    user = data.get("user") or {}
    org = data.get("organization") or {}
    candidates.extend((user.get("projectsV2") or {}).get("nodes") or [])
    candidates.extend((org.get("projectsV2") or {}).get("nodes") or [])

    slug_norm = _normalize_slug(slug)
    for proj in candidates:
        title = proj.get("title") or ""
        if _normalize_slug(title) == slug_norm:
            return proj.get("id")
    return None


def resolve_project_github_id(token: str, slug: str) -> str | None:
    for owner in _get_owner_logins(token):
        pid = _find_project_id_for_owner(token, owner, slug)
        if pid:
            return pid
    return None


def _iter_slugs(argv: list[str]) -> list[str]:
    if not argv:
        return ["agrosmart"]
    slugs: list[str] = []
    for item in argv:
        if "," in item:
            slugs.extend([part.strip() for part in item.split(",") if part.strip()])
        else:
            slugs.append(item)
    return slugs or ["agrosmart"]


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    slugs = _iter_slugs(argv)

    try:
        get_settings.cache_clear()
    except Exception:
        pass
    settings = get_settings()

    exit_code = 0
    for slug in slugs:
        print(f"Looking up project id for slug: {slug}")

        if settings.project_github_ids and slug in settings.project_github_ids:
            pid = settings.project_github_ids.get(slug)
            print(f"Found in PROJECT_GITHUB_IDS: {pid}")
            continue

        if settings.github_project_id:
            print(f"Found GITHUB_PROJECT_ID (default): {settings.github_project_id}")
            continue

        if not settings.github_token:
            print("No GITHUB_TOKEN available in environment. Set it in .env and retry.")
            exit_code = max(exit_code, 2)
            continue

        print(
            "Attempting to resolve via GitHub GraphQL (may require 'project' and 'read:org' scopes)..."
        )
        try:
            pid = resolve_project_github_id(settings.github_token, slug)
            if pid:
                print(f"Resolved ProjectV2 id: {pid}")
                continue
            print("Could not resolve ProjectV2 id automatically.")
            exit_code = max(exit_code, 3)
        except Exception as exc:
            print(f"Resolver failed: {exc}")
            exit_code = max(exit_code, 4)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
