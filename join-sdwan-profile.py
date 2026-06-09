"""
join-sdwan-profile.py

Standalone script: assign a gateway to an SD-WAN profile in the Infinity Portal.

Usage:
    python join-sdwan-profile.py --gateway <gateway-name> --profile <sd-wan-profile-name>
    python join-sdwan-profile.py --gateway hq-exl --profile "SD-WAN Gateways"
    python join-sdwan-profile.py --gateway hq-exl --profile "SD-WAN Gateways" --dry-run
    python join-sdwan-profile.py --gateway hq-exl --profile "SD-WAN Gateways" --debug both

Required .env keys:
    SD_WAN_Client_ID          OAuth clientId
    SD_WAN_Client_SecretKey   OAuth accessKey (secret)
    SD_WAN_Client_Auth_URL    https://cloudinfra-gw.portal.checkpoint.com/auth/external
"""

import argparse
import base64
import json
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        sys.exit(f"ERROR: .env file not found at {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _get_token(env: dict[str, str], debug: str) -> str:
    client_id  = env.get("SD_WAN_Client_ID", "")
    secret_key = env.get("SD_WAN_Client_SecretKey", "")
    auth_url   = env.get("SD_WAN_Client_Auth_URL", "")

    if not client_id or not secret_key or not auth_url:
        sys.exit(
            "ERROR: SD_WAN_Client_ID, SD_WAN_Client_SecretKey and "
            "SD_WAN_Client_Auth_URL must be set in .env"
        )

    body = {"clientId": client_id, "accessKey": secret_key}

    if debug in ("request", "both"):
        masked = {"clientId": client_id, "accessKey": "***"}
        print(f"\n[DEBUG] OAuth REQUEST → {auth_url}")
        print(json.dumps(masked, indent=2))

    resp = requests.post(auth_url, json=body,
                         headers={"Content-Type": "application/json"}, verify=False)

    if debug in ("response", "both"):
        try:
            safe = resp.json()
            if isinstance((safe.get("data") or {}), dict) and "token" in safe["data"]:
                safe["data"] = {**safe["data"], "token": safe["data"]["token"][:16] + "…"}
        except Exception:
            safe = {"raw": resp.text}
        print(f"\n[DEBUG] OAuth RESPONSE  HTTP {resp.status_code}")
        print(json.dumps(safe, indent=2))

    if resp.status_code != 200:
        sys.exit(f"ERROR: OAuth failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    token = (data.get("data") or {}).get("token") or data.get("token", "")
    if not token:
        sys.exit(f"ERROR: No token in OAuth response: {data}")
    return token


def _extract_tenant_id(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) == 3:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded).decode())
            return claims.get("tid") or claims.get("tenantId") or ""
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# GraphQL client
# ---------------------------------------------------------------------------

GRAPHQL_URL = "https://cloudinfra-gw.portal.checkpoint.com/app/sd-wan/graphql/v1"

_token: str = ""
_tenant_id: str = ""
_debug: str = ""


def _gql(query: str, variables: dict | None = None) -> dict:
    body = {"query": query, "variables": variables or {}}

    if _debug in ("request", "both"):
        print(f"\n[DEBUG] REQUEST → {GRAPHQL_URL}")
        print(json.dumps(body, indent=2))

    headers = {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if _tenant_id:
        headers["x-tenant-id"] = _tenant_id

    resp = requests.post(GRAPHQL_URL, json=body, headers=headers)

    if _debug in ("response", "both"):
        print(f"\n[DEBUG] RESPONSE  HTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2))
        except Exception:
            print(resp.text)

    if resp.status_code != 200:
        sys.exit(f"HTTP {resp.status_code}: {resp.text}")

    return resp.json()


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def find_gateway_asset_id(gateway_name: str) -> str:
    resp = _gql("""
        query GetAssets($matchSearch: String) {
            getAssets(matchSearch: $matchSearch) {
                assets { id name }
            }
        }
    """, {"matchSearch": gateway_name})
    assets = ((resp.get("data") or {}).get("getAssets") or {}).get("assets") or []
    for asset in assets:
        name = asset.get("name") or ""
        if name == gateway_name or gateway_name in name:
            return asset["id"]
    # Fall back to listing all
    resp = _gql("query { getAssets { assets { id name } } }")
    assets = ((resp.get("data") or {}).get("getAssets") or {}).get("assets") or []
    for asset in assets:
        if (asset.get("name") or "") == gateway_name:
            return asset["id"]
    names = [a.get("name") for a in assets]
    sys.exit(f"ERROR: Gateway '{gateway_name}' not found in assets: {names}")


def find_profile_id(profile_name: str) -> str:
    resp = _gql("query { getSdWanProfiles { id name } }")
    profiles = (resp.get("data") or {}).get("getSdWanProfiles") or []
    for p in profiles:
        if (p.get("name") or "") == profile_name or profile_name in (p.get("name") or ""):
            return p["id"]
    names = [p.get("name") for p in profiles]
    sys.exit(f"ERROR: SD-WAN profile '{profile_name}' not found: {names}")


def assign_gateway(profile_id: str, gateway_asset_id: str, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] Would add gateway {gateway_asset_id} to profile {profile_id}")
        return

    mutation = """
        mutation UpdateSdWanProfile($id: ID!, $input: SdWanProfileUpdateInput!) {
            updateSdWanProfile(id: $id input: $input)
        }
    """
    variables = {
        "id": profile_id,
        "input": {"addSdWanGateways": [gateway_asset_id], "removeSdWanGateways": []},
    }

    resp = _gql(mutation, variables)
    if (resp.get("data") or {}).get("updateSdWanProfile") is True:
        print("  Gateway assigned to profile.")
        return

    errors = resp.get("errors") or []
    msg = errors[0].get("message") if errors else str(resp)

    if "lock" in msg.lower() or "forbidden-status" in msg.lower():
        print("  Profile locked — discarding session and retrying …")
        _gql("mutation { discardChanges }")
        resp = _gql(mutation, variables)
        if (resp.get("data") or {}).get("updateSdWanProfile") is True:
            print("  Gateway assigned to profile (after discard).")
            return
        errors = resp.get("errors") or []
        msg = errors[0].get("message") if errors else str(resp)

    sys.exit(f"ERROR: Could not assign gateway to profile: {msg}")


def publish(dry_run: bool) -> None:
    if dry_run:
        print("  [dry-run] Would publishChanges")
        return
    resp = _gql("mutation { publishChanges { isValid } }")
    result = ((resp.get("data") or {}).get("publishChanges") or {})
    if result.get("isValid", True):
        print("  Changes published.")
    else:
        print(f"  WARNING: publishChanges isValid=false: {result}")


def enforce(dry_run: bool) -> None:
    if dry_run:
        print("  [dry-run] Would enforcePolicy")
        return
    resp = _gql("mutation { enforcePolicy { id status } }")
    task = (resp.get("data") or {}).get("enforcePolicy") or {}
    print(f"  Policy enforced. Task id={task.get('id')} status={task.get('status')}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assign a gateway to an SD-WAN profile in the Infinity Portal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python join-sdwan-profile.py --gateway hq-exl --profile \"SD-WAN Gateways\"\n"
            "  python join-sdwan-profile.py --gateway hq-exl --profile \"SD-WAN Gateways\" --dry-run\n"
            "  python join-sdwan-profile.py --gateway hq-exl --profile \"SD-WAN Gateways\" --debug both\n"
        ),
    )
    parser.add_argument("--gateway", required=True, help="Gateway name as shown in Infinity Portal")
    parser.add_argument("--profile", required=True, help="SD-WAN profile name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--debug", choices=["request", "response", "both", "all"],
                        default=None, help="Print API request/response bodies")
    parser.add_argument("--env", default=None,
                        help="Path to .env file (default: .env in script directory)")
    args = parser.parse_args()

    global _debug, _token, _tenant_id
    _debug = "both" if args.debug == "all" else (args.debug or "")

    if args.dry_run:
        print("*** DRY-RUN — no changes will be made ***\n")

    env_path = Path(args.env) if args.env else Path(__file__).parent / ".env"
    env = _load_env(env_path)

    print(f"Authenticating to Infinity Portal …")
    _token = _get_token(env, _debug)
    _tenant_id = _extract_tenant_id(_token)
    print("Authentication successful.")

    print(f"\nFinding gateway '{args.gateway}' …")
    gateway_id = find_gateway_asset_id(args.gateway)
    print(f"  Gateway asset ID: {gateway_id}")

    print(f"Finding SD-WAN profile '{args.profile}' …")
    profile_id = find_profile_id(args.profile)
    print(f"  Profile ID: {profile_id}")

    print(f"\nAssigning gateway to profile …")
    assign_gateway(profile_id, gateway_id, args.dry_run)

    print("Publishing changes …")
    publish(args.dry_run)

    print("Enforcing policy …")
    enforce(args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
