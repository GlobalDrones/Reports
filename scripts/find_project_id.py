from __future__ import annotations

import sys
from app.config import get_settings
from app.github_projects import resolve_project_github_id


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    slug = argv[0] if argv else "agrosmart"

    try:
        get_settings.cache_clear()
    except Exception:
        pass
    settings = get_settings()

    print(f"Looking up project id for slug: {slug}")

    if settings.project_github_ids and slug in settings.project_github_ids:
        pid = settings.project_github_ids.get(slug)
        print(f"Found in PROJECT_GITHUB_IDS: {pid}")
        return 0

    if settings.github_project_id:
        print(f"Found GITHUB_PROJECT_ID: {settings.github_project_id}")
        return 0

    if not settings.github_token:
        print("No GITHUB_TOKEN available in environment. Set it in .env and retry.")
        return 2

    print("Attempting to resolve via GitHub GraphQL (may require 'project' and 'read:org' scopes)...")
    try:
        pid = resolve_project_github_id(settings.github_token, slug)
        if pid:
            print(f"Resolved ProjectV2 id: {pid}")
            return 0
        print("Could not resolve ProjectV2 id automatically.")
        return 3
    except Exception as exc:
        print(f"Resolver failed: {exc}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
