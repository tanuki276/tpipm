"""Create and release the Policy Arcade policy in a Rulease v2 workspace."""
from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

POLICY_SOURCE = (Path(__file__).parents[1] / "policy" / "arcade.rulea").read_text()


def fail(response: httpx.Response) -> None:
    if response.is_error:
        raise SystemExit(f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--workspace", default="team")
    parser.add_argument("--project", default="arcade")
    parser.add_argument("--username", default="owner")
    parser.add_argument("--environment", default="development")
    args = parser.parse_args()

    password = getpass.getpass("Rulease password: ")
    client = httpx.Client(base_url=args.url.rstrip("/"), timeout=10)
    response = client.post("/v2/auth/login", json={"username": args.username, "password": password})
    fail(response)
    token = response.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}", "X-Rulease-Workspace": args.workspace}

    response = client.post(
        f"/v2/projects/{args.project}/policies",
        headers=headers,
        json={"key": "admission", "name": "Policy Arcade", "source": POLICY_SOURCE, "context_schema": {}},
    )
    fail(response)
    draft_etag = response.headers["etag"]

    response = client.post(
        f"/v2/projects/{args.project}/policies/admission/revisions",
        headers={**headers, "If-Match": draft_etag},
    )
    fail(response)
    revision = response.json()["revision"]

    response = client.get(f"/v2/projects/{args.project}/environments/{args.environment}", headers=headers)
    fail(response)
    environment_etag = response.headers["etag"]

    response = client.post(
        f"/v2/projects/{args.project}/deployments",
        headers={**headers, "If-Match": environment_etag},
        json={"environment": args.environment, "items": {"admission": revision}},
    )
    fail(response)
    deployment_id = response.json()["deployment_id"]

    response = client.post(f"/v2/deployments/{deployment_id}/release", headers=headers)
    fail(response)

    response = client.post(
        "/v2/keys",
        headers=headers,
        json={
            "service_account": "policy-arcade-web",
            "permissions": ["decision:execute"],
            "scope": {"project": args.project, "policy": "admission", "environment": args.environment},
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        },
    )
    fail(response)
    api_key = response.json()["key"]

    print("\nRulease is ready. Export these for the web app:\n")
    print(f"RULEASE_URL={args.url.rstrip('/')}")
    print(f"RULEASE_API_KEY={api_key}")
    print(f"RULEASE_WORKSPACE={args.workspace}")
    print(f"RULEASE_PROJECT={args.project}")
    print("RULEASE_POLICY=admission")
    print(f"RULEASE_ENVIRONMENT={args.environment}")


if __name__ == "__main__":
    main()
