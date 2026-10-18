"""Print the Spotify account plan and the reason on a failed play."""

from __future__ import annotations

import json

import httpx

from fluffy.integrations.spotify import TOKEN_PATH, _error_reason


def main() -> None:
    if not TOKEN_PATH.is_file():
        print("no Spotify token at", TOKEN_PATH)
        return
    token = json.loads(TOKEN_PATH.read_text())
    access = token.get("access_token") or ""
    if not access:
        print("token file has no access_token; log in again")
        return
    response = httpx.get(
        "https://api.spotify.com/v1/me",
        headers={"Authorization": f"Bearer {access}"},
        timeout=15,
    )
    if response.status_code >= 400:
        print("account lookup failed:", response.status_code, _error_reason(response) or response.text[:200])
        return
    body = response.json()
    product = (body.get("product") or "unknown")
    print(f"account: {body.get('display_name') or body.get('id')}")
    print(f"plan: {product}")


if __name__ == "__main__":
    main()
