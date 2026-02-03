from __future__ import annotations

import sys
import unicodedata

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


def _resolve_project_id_by_org_number(token: str, org: str, number: int) -> str | None:
        query = """
        query($login: String!, $num: Int!) {
            organization(login: $login) {
                projectV2(number: $num) { id title }
            }
        }
        """
        data = _graphql(token, query, {"login": org, "num": number})
        org_node = data.get("organization") or {}
        project = org_node.get("projectV2") or {}
        return project.get("id")


def _resolve_project_id_by_repo_number(token: str, owner: str, repo: str, number: int) -> str | None:
        query = """
        query($owner: String!, $repo: String!, $num: Int!) {
            repository(owner: $owner, name: $repo) {
                projectV2(number: $num) { id title }
            }
        }
        """
        data = _graphql(token, query, {"owner": owner, "repo": repo, "num": number})
        repo_node = data.get("repository") or {}
        project = repo_node.get("projectV2") or {}
        return project.get("id")


def _parse_target(value: str) -> tuple[str, dict[str, object]]:
        parts = [part for part in value.split("/") if part]
        if len(parts) >= 3 and parts[0].lower() in {"org", "organization"}:
                return "org", {"org": parts[1], "number": parts[2]}
        if len(parts) >= 4 and parts[0].lower() in {"repo", "repository"}:
                return "repo", {"owner": parts[1], "repo": parts[2], "number": parts[3]}
        return "slug", {"slug": value}


def _iter_targets(argv: list[str]) -> list[str]:
    if not argv:
        return []
    targets: list[str] = []
    for item in argv:
        if "," in item:
            targets.extend([part.strip() for part in item.split(",") if part.strip()])
        else:
            targets.append(item)
    return targets


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    targets = _iter_targets(argv)

    try:
        get_settings.cache_clear()
    except Exception:
        pass
    settings = get_settings()

    if not targets:
        print("Usage: python scripts/find_project_id.py org/<ORG>/<NUMBER>[,org/<ORG>/<NUMBER>...] repo/<OWNER>/<REPO>/<NUMBER>")
        return 1

    exit_code = 0
    for target in targets:
        target_type, payload = _parse_target(target)

        if target_type == "org":
            org = str(payload.get("org"))
            number_raw = payload.get("number")
            print(f"Looking up project id for org/number: {org}/{number_raw}")
        elif target_type == "repo":
            owner = str(payload.get("owner"))
            repo = str(payload.get("repo"))
            number_raw = payload.get("number")
            print(f"Looking up project id for repo/number: {owner}/{repo}/{number_raw}")
        else:
            print(
                "Unsupported target. Use org/<ORG>/<NUMBER> or repo/<OWNER>/<REPO>/<NUMBER>."
            )
            exit_code = max(exit_code, 5)
            continue

        if not settings.github_token:
            print("No GITHUB_TOKEN available in environment. Set it in .env and retry.")
            exit_code = max(exit_code, 2)
            continue

        print(
            "Attempting to resolve via GitHub GraphQL (may require 'project' and 'read:org' scopes)..."
        )
        try:
            pid = None
            if target_type == "org":
                pid = _resolve_project_id_by_org_number(
                    settings.github_token, org, int(str(number_raw))
                )
            elif target_type == "repo":
                pid = _resolve_project_id_by_repo_number(
                    settings.github_token, owner, repo, int(str(number_raw))
                )
            else:
                pid = None
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
