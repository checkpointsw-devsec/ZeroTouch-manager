"""
SD-WAN Service
Assigns a gateway to an SD-WAN profile in the Check Point Infinity Portal.

Async port of the standalone ``join-sdwan-profile.py`` script. Authenticates to
the Infinity Portal via OAuth and performs the SD-WAN GraphQL operations:
find gateway asset, find profile, assign gateway, publish, enforce.
"""
import base64
import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from app.config import settings, log_http_request, log_http_response


class SDWANService:
    """Service for assigning gateways to SD-WAN profiles in the Infinity Portal."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        auth_url: Optional[str] = None,
        graphql_url: Optional[str] = None,
    ):
        self.client_id = client_id or settings.sd_wan_client_id
        self.secret_key = secret_key or settings.sd_wan_client_secretkey
        self.auth_url = auth_url or settings.sd_wan_client_auth_url
        self.graphql_url = graphql_url or settings.sd_wan_graphql_url
        self.token: str = ""
        self.tenant_id: str = ""
        self.client = httpx.AsyncClient(timeout=60.0, verify=settings.ssl_verify)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    async def login(self) -> None:
        """Authenticate to the Infinity Portal and store the bearer token."""
        if not self.client_id or not self.secret_key or not self.auth_url:
            raise Exception(
                "SD-WAN credentials are not configured. Set SD_WAN_Client_ID, "
                "SD_WAN_Client_SecretKey and SD_WAN_Client_Auth_URL in the backend .env file."
            )

        body = {"clientId": self.client_id, "accessKey": self.secret_key}
        log_http_request(logger.info, "sd-wan auth", {"clientId": self.client_id, "accessKey": "***"})

        resp = await self.client.post(
            self.auth_url, json=body, headers={"Content-Type": "application/json"}
        )
        log_http_response(logger.info, "sd-wan auth", resp.status_code, resp.text)

        if resp.status_code != 200:
            raise Exception(f"SD-WAN OAuth failed (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        token = (data.get("data") or {}).get("token") or data.get("token", "")
        if not token:
            raise Exception(f"No token in SD-WAN OAuth response: {data}")

        self.token = token
        self.tenant_id = self._extract_tenant_id(token)
        logger.info("SD-WAN authentication successful")

    @staticmethod
    def _extract_tenant_id(token: str) -> str:
        """Decode the JWT payload to extract the tenant id (best-effort)."""
        try:
            parts = token.split(".")
            if len(parts) == 3:
                padded = parts[1] + "=" * (-len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(padded).decode())
                return claims.get("tid") or claims.get("tenantId") or ""
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # GraphQL client
    # ------------------------------------------------------------------
    async def _gql(self, query: str, variables: Optional[dict] = None) -> dict:
        body = {"query": query, "variables": variables or {}}
        log_http_request(logger.debug, "sd-wan graphql", body)

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.tenant_id:
            headers["x-tenant-id"] = self.tenant_id

        resp = await self.client.post(self.graphql_url, json=body, headers=headers)
        log_http_response(logger.debug, "sd-wan graphql", resp.status_code, resp.text)

        if resp.status_code != 200:
            raise Exception(f"SD-WAN GraphQL HTTP {resp.status_code}: {resp.text}")

        return resp.json()

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    async def find_gateway_asset_id(self, gateway_name: str) -> str:
        """Resolve a gateway name to its SD-WAN asset id."""
        resp = await self._gql(
            """
            query GetAssets($matchSearch: String) {
                getAssets(matchSearch: $matchSearch) {
                    assets { id name }
                }
            }
            """,
            {"matchSearch": gateway_name},
        )
        assets = ((resp.get("data") or {}).get("getAssets") or {}).get("assets") or []
        for asset in assets:
            name = asset.get("name") or ""
            if name == gateway_name or gateway_name in name:
                return asset["id"]

        # Fall back to listing all assets
        resp = await self._gql("query { getAssets { assets { id name } } }")
        assets = ((resp.get("data") or {}).get("getAssets") or {}).get("assets") or []
        for asset in assets:
            if (asset.get("name") or "") == gateway_name:
                return asset["id"]

        names = [a.get("name") for a in assets]
        raise Exception(f"Gateway '{gateway_name}' not found in SD-WAN assets: {names}")

    async def find_profile_id(self, profile_name: str) -> str:
        """Resolve an SD-WAN profile name to its id."""
        resp = await self._gql("query { getSdWanProfiles { id name } }")
        profiles = (resp.get("data") or {}).get("getSdWanProfiles") or []
        for p in profiles:
            if (p.get("name") or "") == profile_name or profile_name in (p.get("name") or ""):
                return p["id"]
        names = [p.get("name") for p in profiles]
        raise Exception(f"SD-WAN profile '{profile_name}' not found: {names}")

    async def assign_gateway(self, profile_id: str, gateway_asset_id: str) -> None:
        """Add a gateway asset to an SD-WAN profile (with lock-retry)."""
        mutation = """
            mutation UpdateSdWanProfile($id: ID!, $input: SdWanProfileUpdateInput!) {
                updateSdWanProfile(id: $id input: $input)
            }
        """
        variables = {
            "id": profile_id,
            "input": {"addSdWanGateways": [gateway_asset_id], "removeSdWanGateways": []},
        }

        resp = await self._gql(mutation, variables)
        if (resp.get("data") or {}).get("updateSdWanProfile") is True:
            return

        errors = resp.get("errors") or []
        msg = errors[0].get("message") if errors else str(resp)

        if "lock" in msg.lower() or "forbidden-status" in msg.lower():
            logger.info("SD-WAN profile locked — discarding session and retrying")
            await self._gql("mutation { discardChanges }")
            resp = await self._gql(mutation, variables)
            if (resp.get("data") or {}).get("updateSdWanProfile") is True:
                return
            errors = resp.get("errors") or []
            msg = errors[0].get("message") if errors else str(resp)

        raise Exception(f"Could not assign gateway to SD-WAN profile: {msg}")

    async def publish(self) -> None:
        """Publish pending SD-WAN changes."""
        resp = await self._gql("mutation { publishChanges { isValid } }")
        result = (resp.get("data") or {}).get("publishChanges") or {}
        if not result.get("isValid", True):
            logger.warning(f"SD-WAN publishChanges isValid=false: {result}")

    async def enforce(self) -> Dict[str, Any]:
        """Enforce the SD-WAN policy."""
        resp = await self._gql("mutation { enforcePolicy { id status } }")
        return (resp.get("data") or {}).get("enforcePolicy") or {}

    # ------------------------------------------------------------------
    # High-level orchestration
    # ------------------------------------------------------------------
    async def join_profile(self, gateway_name: str, profile_name: str) -> Dict[str, Any]:
        """
        Full workflow: assign *gateway_name* to SD-WAN *profile_name*,
        publish and enforce.

        Returns a result dict with a human-readable ``steps`` list.
        """
        steps: List[str] = []

        await self.login()
        steps.append("Authenticated to Infinity Portal")

        gateway_id = await self.find_gateway_asset_id(gateway_name)
        steps.append(f"Found gateway asset '{gateway_name}'")

        profile_id = await self.find_profile_id(profile_name)
        steps.append(f"Found SD-WAN profile '{profile_name}'")

        await self.assign_gateway(profile_id, gateway_id)
        steps.append("Gateway assigned to SD-WAN profile")

        await self.publish()
        steps.append("Changes published")

        task = await self.enforce()
        steps.append(f"Policy enforced (task id={task.get('id')}, status={task.get('status')})")

        return {
            "success": True,
            "gateway_name": gateway_name,
            "profile_name": profile_name,
            "gateway_id": gateway_id,
            "profile_id": profile_id,
            "steps": steps,
            "enforce_task": task,
        }
