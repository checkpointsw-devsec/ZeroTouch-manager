"""
Smart-1 Cloud Management API Service.
Handles gateway creation using Check Point Management API on Smart-1 Cloud.
Uses the standard Management API (add-simple-gateway) with cloud-specific features.
"""
import httpx
import asyncio
import inspect
import time
from typing import Dict, Any, Optional, List, Callable
from loguru import logger

from ..config import settings
from ..config import log_http_request, log_http_response


class Smart1CloudService:
    """
    Service for interacting with Check Point Smart-1 Cloud Management API.
    Uses the standard Management API hosted on Smart-1 Cloud.
    """

    # Spark (SMB) hardware model identifiers — used to auto-detect OS version
    SPARK_HARDWARE_MODELS = ("1500","1530","1570","1590","1535","1550","1555","1575","1595","1575R","1595R","1600","1800","1900","2000","2530","2550","2570","2580","2590")

    def __init__(self):
        # Smart-1 Cloud Management API endpoint
        # Format: https://{tenant}.maas.checkpoint.com or https://{tenant}.maas.checkpoint.com/{context-id}/web_api
        base = (settings.smart1_cloud_base_url or "").rstrip('/')
        # Strip /web_api suffix if present (we add it in method calls)
        if base.endswith('/web_api'):
            base = base[:-8]
        self.base_url = base
        self.api_key = getattr(settings, 'smart1_cloud_api_key', None) or settings.smart1_cloud_secret_key
        self.session_id: Optional[str] = None
        self.api_server_version: Optional[str] = None
        self.client = httpx.AsyncClient(verify=settings.ssl_verify, timeout=60.0)
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - logout and close HTTP client."""
        if self.session_id:
            try:
                await self.logout()
            except Exception as e:
                logger.warning(f"Logout failed during cleanup: {e}")
        await self.client.aclose()
    
    async def login(self) -> str:
        """
        Login to Smart-1 Cloud Management API using API key authentication.
        
        Returns:
            Session ID (sid) string
            
        Raises:
            httpx.HTTPError: If login fails
        """
        try:
            logger.info("Logging in to Smart-1 Cloud Management API")
            
            url = f"{self.base_url}/web_api/login"
            payload = {
                "api-key": self.api_key
            }
            
            logger.info(f"POST {url}")
            logger.debug(f"API Key: {self.api_key[:10]}..." if self.api_key else "API Key: NOT SET")

            headers = {
                "Content-Type": "application/json"
            }

            log_http_request(logger.info, "login", payload)
            response = await self.client.post(url, json=payload, headers=headers)

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "login", response.status_code, response.text)

            response.raise_for_status()

            data = response.json()

            # Extract session ID from response
            self.session_id = data.get("sid")
            self.api_server_version = data.get("api-server-version")
            
            if not self.session_id:
                logger.error(f"Session ID not found in response: {data}")
                raise ValueError("Session ID (sid) not found in login response")
            
            logger.info(f"Successfully logged in. API Version: {self.api_server_version}")
            logger.debug(f"Session ID: {self.session_id[:20]}...")
            return self.session_id
            
        except httpx.TimeoutException:
            logger.error(f"Smart-1 Cloud login timed out after {self.client.timeout.connect}s connecting to {url}")
            raise
        except httpx.HTTPError as e:
            detail = str(e) or type(e).__name__
            logger.error(f"Smart-1 Cloud login failed: {detail}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise
    
    async def logout(self) -> Dict[str, Any]:
        """
        Logout from Smart-1 Cloud Management API.
        
        Returns:
            Logout response
        """
        try:
            if not self.session_id:
                logger.warning("No active session to logout")
                return {"message": "No active session"}
            
            logger.info("Logging out from Smart-1 Cloud")

            url = f"{self.base_url}/web_api/logout"
            headers = self._get_headers()

            log_http_request(logger.info, "logout", {})
            response = await self.client.post(url, json={}, headers=headers)
            log_http_response(logger.info, "logout", response.status_code, response.text)
            response.raise_for_status()
            
            result = response.json()
            self.session_id = None
            
            logger.info("Successfully logged out")
            return result
            
        except httpx.HTTPError as e:
            logger.error(f"Logout failed: {str(e)}")
            raise
    
    def _get_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers with session ID for authenticated requests.
        
        Returns:
            Dictionary of HTTP headers
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Please login first.")
        
        return {
            "X-chkp-sid": self.session_id,
            "Content-Type": "application/json"
        }

    async def _api_call(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        label: str = "",
        retry_on_401: bool = True,
    ) -> Dict[str, Any]:
        """
        Generic Management API call with automatic 401-retry (session refresh).

        Args:
            endpoint: API endpoint path (e.g. "publish", "show-simple-gateway")
            payload: JSON body
            label: Label for logging (defaults to endpoint name)
            retry_on_401: Re-login and retry once on HTTP 401 (default: True)

        Returns:
            Parsed JSON response

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retry if applicable)
        """
        label = label or endpoint
        for attempt in range(2 if retry_on_401 else 1):
            try:
                url = f"{self.base_url}/web_api/{endpoint}"
                headers = self._get_headers()
                log_http_request(logger.info, label, payload)
                response = await self.client.post(url, headers=headers, json=payload)
                log_http_response(logger.info, label, response.status_code, response.text)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401 and attempt == 0 and retry_on_401:
                    logger.warning(f"{label} got 401 — session expired, re-logging in...")
                    await self._relogin()
                    continue
                raise

    async def create_gateway(
        self,
        gateway_name: str,
        sic_otp: str,
        gateway_type: Optional[str] = None,
        identification_method: Optional[str] = None,
        maas_token: Optional[str] = None,
        version: Optional[str] = None,
        hardware: Optional[str] = None,
        interfaces: Optional[list] = None,
        topology_mode: Optional[str] = None,
        auto_generate_ip: Optional[bool] = False,
        ip_address: Optional[str] = None,
        allow_smb: bool = True,
        # Security blades
        firewall: bool = True,
        vpn: bool = True,
        ips: bool = True,
        application_control: bool = True,
        url_filtering: bool = True,
        anti_bot: bool = True,
        anti_virus: bool = True,
        threat_emulation: bool = True,
        content_awareness: bool = False,
        # VPN community
        vpn_community: Optional[str] = None,
        vpn_role: str = "satellite"
    ) -> Dict[str, Any]:
        """
        Create a simple gateway object in Smart-1 Cloud using add-simple-gateway API.
        
        Args:
            gateway_name: Name for the gateway
            sic_otp: Secure Internal Communication one-time password
            gateway_type: Type of gateway (e.g. "APPLIANCE_OR_OPENSERVER")
            identification_method: How to identify the gateway (e.g. "GATEWAY_NAME")
            maas_token: MaaS authentication token (optional)
            version: OS version (R81.10 or R82). If not provided, auto-detects based on hardware.
            hardware: Optional hardware type (omitted if not specified)
            interfaces: Optional list of gateway interfaces
            topology_mode: Optional topology settings mode
            auto_generate_ip: Auto-generate IP address for gateway (DAIP)
            ip_address: Optional IP address for the gateway
            allow_smb: Whether to allow SMB protocol (default: True)
            firewall: Enable Firewall blade (default: True)
            vpn: Enable VPN blade (default: True)
            ips: Enable IPS blade (default: True)
            application_control: Enable Application Control blade (default: True)
            url_filtering: Enable URL Filtering blade (default: True)
            anti_bot: Enable Anti-Bot blade (default: True)
            anti_virus: Enable Anti-Virus blade (default: True)
            threat_emulation: Enable Threat Emulation blade (default: True)
            content_awareness: Enable Content Awareness blade (default: False)
            vpn_community: Optional VPN community to add gateway to
            vpn_role: Gateway role in VPN community - "center" or "satellite" (default: "satellite")
            
        Returns:
            Gateway creation response with cloud_token in format:
            {
                "uid": "...",
                "name": "gateway_name",
                "cloud_token": "XXXXXX...",  # This is the MaaS token for Zero Touch
                ...
            }
            
        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"Creating gateway '{gateway_name}' in Smart-1 Cloud")
            
            url = f"{self.base_url}/web_api/add-simple-gateway"
            headers = self._get_headers()
            
            # Determine version: use provided version or fallback logic
            is_spark = False
            if version:
                # Use provided version
                if version in ["R81.10", "R80.20"]:
                    is_spark = True
                logger.info(f"Using provided OS version: {version}")
            elif hardware:
                hardware_lower = hardware.lower()
                if any(x in hardware_lower for x in self.SPARK_HARDWARE_MODELS):
                    version = "R81.10"
                    is_spark = True
                else:
                    version = "R82"
            else:
                version = "R82"

            if auto_generate_ip:
                # Auto-IP flow (Spark or Gaia): add-simple-gateway with name + auto-generate-ip only.
                # version, hardware, trust-method, allow-smb are set via set-simple-gateway / set-trust.
                payload = {
                    "name": gateway_name,
                    "auto-generate-ip": True,
                }
            else:
                payload = {
                    "name": gateway_name,
                    "version": version,
                    "firewall": firewall,
                    "vpn": vpn,
                    "application-control": application_control,
                    "url-filtering": url_filtering,
                    "ips": ips,
                    "anti-bot": anti_bot,
                    "anti-virus": anti_virus,
                    "threat-emulation": threat_emulation,
                    "content-awareness": content_awareness
                }

                if is_spark:
                    payload["trust-method"] = "cloud_token"
                else:
                    # Gaia: cloud_token trust with OTP
                    payload["trust-method"] = "cloud_token"
                    payload["one-time-password"] = sic_otp

                if hardware and hardware.strip():
                    payload["hardware"] = hardware

                # Add interfaces if provided
                if interfaces:
                    payload["interfaces"] = interfaces

                # Add topology mode if provided
                if topology_mode:
                    payload["interfaces-topology-settings"] = topology_mode

                payload["auto-generate-ip"] = bool(auto_generate_ip)

                # Add SMB settings (only for Spark gateways)
                if allow_smb and is_spark:
                    payload["allow-smb"] = True
            
            logger.info(f"POST {url}")
            log_http_request(logger.info, "add-simple-gateway", payload)

            response = await self.client.post(url, headers=headers, json=payload)

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "add-simple-gateway", response.status_code, response.text)

            response.raise_for_status()

            result = response.json()

            # Extract authentication-token from trust-details (MaaS token for Zero Touch)
            # Response format: {"trust-details": {"authentication-token": "...", ...}, ...}
            trust_details = result.get("trust-details", {})
            auth_token = trust_details.get("authentication-token")
            
            if auth_token:
                logger.info(f"Successfully created gateway '{gateway_name}' with authentication-token")
                logger.debug(f"Authentication token: {auth_token[:50]}...")
                # Add cloud_token to result for easier access by callers
                result["cloud_token"] = auth_token
            else:
                logger.warning("Gateway created but no authentication-token in trust-details")
                logger.debug(f"trust-details: {trust_details}")
            
            return result
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to create gateway: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                try:
                    error_detail = e.response.json()
                    logger.error(f"API Error: {error_detail}")
                    
                    # Check for common errors
                    error_message = str(error_detail.get('message', ''))
                    if 'already exists' in error_message.lower():
                        raise Exception(
                            f"Gateway '{gateway_name}' already exists in Smart-1 Cloud. "
                            "Please delete the existing gateway or use a different name."
                        )
                except ValueError:
                    logger.error(f"Response text: {e.response.text}")
            raise

    async def set_simple_gateway(
        self,
        gateway_name: str,
        version: Optional[str] = None,
        hardware: Optional[str] = None,
        allow_smb: bool = False,
        **extra_fields
    ) -> Dict[str, Any]:
        """
        Call set-simple-gateway to configure version, hardware, and optionally allow-smb.
        Spark gateways: pass allow_smb=True.
        Gaia gateways: allow_smb must NOT be sent (leave as default False).
        """
        try:
            url = f"{self.base_url}/web_api/set-simple-gateway"
            headers = self._get_headers()
            payload: Dict[str, Any] = {"name": gateway_name}
            if version:
                payload["version"] = version
            if hardware:
                payload["hardware"] = hardware
            if allow_smb:
                payload["allow-smb"] = True
            payload.update(extra_fields)

            logger.info(f"Calling set-simple-gateway for gateway '{gateway_name}'")
            logger.info(f"POST {url}")
            log_http_request(logger.info, "set-simple-gateway", payload)

            response = await self.client.post(url, headers=headers, json=payload)
            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "set-simple-gateway", response.status_code, response.text)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            logger.error(f"set-simple-gateway failed: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise

    async def set_trust(
        self,
        gateway_name: str,
        trust_method: str = "cloud_token",
        one_time_password: Optional[str] = None,
        initiation_phase: Optional[str] = None,
        allow_smb: bool = False,
    ) -> Dict[str, Any]:
        """
        Call set-trust on an existing gateway object.

        For Spark OTP step:
            trust_method omitted, one_time_password=<sic-key>,
            initiation_phase="when_gateway_connects", allow_smb=True

        For Spark cloud_token step (MaaS token):
            trust_method="cloud_token", allow_smb=True

        For Gaia cloud_token step:
            trust_method="cloud_token", allow_smb=False (must NOT be sent)

        Returns:
            set-trust response with cloud_token added at top level when present.
        """
        try:
            url = f"{self.base_url}/web_api/set-trust"
            headers = self._get_headers()
            payload: Dict[str, Any] = {"name": gateway_name}
            if trust_method:
                payload["trust-method"] = trust_method
            if one_time_password:
                payload["one-time-password"] = one_time_password
            if initiation_phase:
                payload["trust-settings"] = {"initiation-phase": initiation_phase}
            if allow_smb:
                payload["allow-smb"] = True

            logger.info(f"Calling set-trust for gateway '{gateway_name}' (trust-method={trust_method})")
            logger.info(f"POST {url}")
            log_http_request(logger.info, "set-trust", payload)

            response = await self.client.post(url, headers=headers, json=payload)
            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "set-trust", response.status_code, response.text)
            response.raise_for_status()

            result = response.json()
            trust_details = result.get("trust-details", {})
            auth_token = trust_details.get("authentication-token")
            if auth_token:
                result["cloud_token"] = auth_token
                logger.info("set-trust returned authentication-token successfully")
            else:
                logger.warning("set-trust response has no authentication-token in trust-details")
            return result

        except httpx.HTTPError as e:
            logger.error(f"set-trust failed: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise

    async def configure_vpn_interface(
        self,
        gateway_name: str,
        is_spark: bool = False
    ) -> Dict[str, Any]:
        """
        Configure VPN settings for the gateway's external interface.
        Spark gateways use WAN interface, Gaia gateways use eth1.
        This is required before adding the gateway to a VPN community.
        
        Args:
            gateway_name: Gateway name
            is_spark: Whether this is a Spark gateway (enables allow-smb)
            
        Returns:
            set-simple-gateway response
            
        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"Configuring VPN settings for gateway '{gateway_name}'")
            
            url = f"{self.base_url}/web_api/set-simple-gateway"
            headers = self._get_headers()
            
            # Spark gateways use WAN interface, Gaia gateways use eth1
            interface_name = "WAN" if is_spark else "eth1"
            logger.info(f"Using interface '{interface_name}' for VPN domain ({'Spark' if is_spark else 'Gaia'} gateway)")
            
            payload = {
                "name": gateway_name,
                "vpn-settings": {
                    "vpn-domain-type": "addresses_behind_gw",
                    "interfaces": [
                        {
                            "interface-name": interface_name
                        }
                    ]
                }
            }
            
            # Add allow-smb only for Spark gateways
            if is_spark:
                payload["allow-smb"] = True
            
            logger.info(f"POST {url}")
            log_http_request(logger.info, "configure-vpn-interface", payload)

            response = await self.client.post(url, headers=headers, json=payload)

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "configure-vpn-interface", response.status_code, response.text)

            response.raise_for_status()

            result = response.json()
            logger.info(f"Successfully configured VPN interface for gateway '{gateway_name}'")
            
            return result
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to configure VPN interface: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise
    
    async def publish(self) -> Dict[str, Any]:
        """
        Publish pending changes in Smart-1 Cloud.
        
        Returns:
            Publish response with task-id
            
        Raises:
            httpx.HTTPError: If request fails
        """
        logger.info("Publishing changes in Smart-1 Cloud")
        result = await self._api_call("publish", {})
        task_id = result.get("task-id")

        if task_id:
            logger.info(f"Publish initiated with task-id: {task_id}, waiting for completion...")
            await self.wait_for_task(task_id, timeout=120, poll_interval=5)
            logger.info("Publish task completed")
        else:
            logger.info("Publish completed (no task-id returned)")

        return result
    
    async def add_gateway_to_vpn_community(
        self,
        gateway_name: str,
        community_name: str,
        role: str = "satellite"
    ) -> Dict[str, Any]:
        """
        Add gateway to a VPN community.

        Args:
            gateway_name: Gateway name
            community_name: VPN community name
            role: Gateway role - "center" or "satellite"

        Returns:
            Update response
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        try:
            logger.info(f"Adding gateway '{gateway_name}' to VPN community '{community_name}' as '{role}'")

            # First, get the community details
            url = f"{self.base_url}/web_api/show-vpn-community-star"
            headers = self._get_headers()

            log_http_request(logger.info, "show-vpn-community-star", {"name": community_name})
            response = await self.client.post(
                url,
                headers=headers,
                json={"name": community_name}
            )
            log_http_response(logger.info, "show-vpn-community-star", response.status_code, response.text)

            if response.status_code == 404:
                logger.warning(f"VPN community '{community_name}' not found")
                raise ValueError(f"VPN community '{community_name}' not found in Smart-1 Cloud")

            response.raise_for_status()
            # Community verified to exist — proceed with update

            # Update the community to add the gateway
            url = f"{self.base_url}/web_api/set-vpn-community-star"

            # Build the update payload based on role
            if role.lower() == "center":
                payload = {
                    "name": community_name,
                    "center-gateways": {
                        "add": gateway_name
                    },
                    "ignore-warnings": True
                }
            else:
                # Default to satellite
                payload = {
                    "name": community_name,
                    "satellite-gateways": {
                        "add": gateway_name
                    },
                    "ignore-warnings": True
                }

            logger.debug(f"POST {url}")
            log_http_request(logger.info, "set-vpn-community-star", payload)

            response = await self.client.post(url, headers=headers, json=payload)

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "set-vpn-community-star", response.status_code, response.text)

            response.raise_for_status()

            result = response.json()
            logger.info(f"Successfully added gateway to VPN community")

            return result

        except httpx.HTTPError as e:
            logger.error(f"Failed to add gateway to VPN community: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
                
                # Check for Enhanced Link Selection error (Spark gateways)
                try:
                    error_detail = e.response.json()
                    errors = error_detail.get("errors", [])
                    for err in errors:
                        if "Enhanced Link Selection" in err.get("message", ""):
                            raise ValueError(
                                f"Cannot add gateway '{gateway_name}' to VPN community '{community_name}'. "
                                "Spark gateways (1100/1200R/1430-1590 series) use R81.10 which doesn't support "
                                "Enhanced Link Selection. Please use a VPN community configured in legacy mode."
                            )
                except ValueError:
                    raise
                except Exception as parse_err:
                    logger.debug(f"Could not parse error response: {parse_err}")
            raise

    async def show_task(self, task_id: str) -> Dict[str, Any]:
        """
        Check the status of an async task.
        
        Args:
            task_id: Task ID to check
            
        Returns:
            Task status response
        """
        return await self._api_call("show-task", {"task-id": task_id})
    
    async def wait_for_task(self, task_id: str, timeout: int = 300, poll_interval: int = 5) -> Dict[str, Any]:
        """
        Wait for an async task to complete.
        
        Args:
            task_id: Task ID to wait for
            timeout: Maximum time to wait in seconds
            poll_interval: Time between status checks in seconds
            
        Returns:
            Final task status
        """
        start_time = time.monotonic()
        
        while True:
            result = await self.show_task(task_id)
            
            tasks = result.get("tasks", [])
            if tasks:
                task = tasks[0]
                status = task.get("status")
                progress = task.get("progress-percentage", 0)
                
                logger.info(f"Task {task_id}: {status} ({progress}%)")
                
                if status in ["succeeded", "completed"]:
                    return result
                elif status in ["failed", "discarded"]:
                    raise Exception(f"Task failed: {task.get('suppressed-info', {}).get('message', status)}")
            
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"Task {task_id} did not complete within {timeout} seconds")
            
            await asyncio.sleep(poll_interval)
    
    async def get_gateway(self, gateway_name: str) -> Dict[str, Any]:
        """
        Get gateway details from Smart-1 Cloud using show-simple-gateway.
        
        Args:
            gateway_name: Gateway name
            
        Returns:
            Gateway details
            
        Raises:
            httpx.HTTPError: If request fails
        """
        logger.info(f"Fetching gateway '{gateway_name}' from Smart-1 Cloud")
        result = await self._api_call("show-simple-gateway", {"name": gateway_name})
        logger.info(f"Retrieved gateway '{gateway_name}'")
        return result
    
    async def list_gateways(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """
        List all simple gateways in Smart-1 Cloud.
        
        Args:
            limit: Maximum number of results
            offset: Results offset
            
        Returns:
            List of gateway objects
            
        Raises:
            httpx.HTTPError: If request fails
        """
        logger.info("Listing gateways in Smart-1 Cloud")
        result = await self._api_call(
            "show-simple-gateways",
            {"limit": limit, "offset": offset},
        )
        gateways = result.get("objects", [])
        total = result.get("total", len(gateways))
        logger.info(f"Retrieved {len(gateways)} of {total} gateway(s)")
        return gateways
    
    async def install_policy(
        self,
        gateway_name: str,
        policy_package: str = "Standard",
        access: bool = True,
        threat_prevention: bool = False
    ) -> Dict[str, Any]:
        """
        Install policy on a gateway.
        
        Args:
            gateway_name: Target gateway name
            policy_package: Policy package name
            access: Install access policy
            threat_prevention: Install threat prevention policy
            
        Returns:
            Install policy response with task-id
        """
        logger.info(f"Installing policy '{policy_package}' on gateway '{gateway_name}'")
        payload = {
            "policy-package": policy_package,
            "targets": [gateway_name],
            "access": access,
            "threat-prevention": threat_prevention
        }
        result = await self._api_call("install-policy", payload)
        task_id = result.get("task-id")
        logger.info(f"Policy install initiated with task-id: {task_id}")
        return result
    
    async def show_gateway_capabilities(
        self,
        hardware: Optional[str] = None,
        hardware_subtype: Optional[str] = None,
        platform: Optional[str] = None,
        version: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get gateway capabilities including supported hardware, platforms, versions.
        
        Args:
            hardware: Filter by specific hardware
            hardware_subtype: Filter by hardware subtype (wired, dsl, wireless, etc.)
            platform: Filter by platform (smb, quantum, maestro, open server, etc.)
            version: Filter by version
            
        Returns:
            Gateway capabilities including:
            - supported-hardware
            - supported-platforms
            - supported-versions
            - supported-blades
            - supported-hardware-subtypes
        """
        logger.info("Fetching gateway capabilities from Smart-1 Cloud")

        payload = {}
        if hardware:
            payload["hardware"] = hardware
        if hardware_subtype:
            payload["hardware-subtype"] = hardware_subtype
        if platform:
            payload["platform"] = platform
        if version:
            payload["version"] = version

        result = await self._api_call("show-gateway-capabilities", payload)

        # Log summary of capabilities
        hardware_list = result.get("supported-hardware", {}).get("hardware", [])
        platforms = result.get("supported-platforms", {}).get("platform", [])
        logger.info(f"Retrieved {len(hardware_list)} hardware options, {len(platforms)} platforms")
        
        return result

    async def set_gateway_sic_password(
        self,
        gateway_name: str,
        one_time_password: str
    ) -> Dict[str, Any]:
        """
        Set the SIC one-time-password for a gateway using set-simple-gateway API.
        
        This is used after the gateway has completed initial deployment to set
        the actual SIC password for trust establishment.
        
        Args:
            gateway_name: Name of the gateway
            one_time_password: The SIC one-time password to set
            
        Returns:
            Update response
            
        Raises:
            httpx.HTTPError: If request fails
        """
        logger.info(f"Setting SIC one-time-password for gateway '{gateway_name}'")
        payload = {
            "name": gateway_name,
            "one-time-password": one_time_password
        }
        result = await self._api_call("set-simple-gateway", payload, "set-sic-password")
        logger.info(f"Successfully set SIC one-time-password for gateway '{gateway_name}'")
        return result

    async def wait_for_deployment_status(
        self,
        gateway_name: str,
        target_statuses: List[str] = None,
        timeout: int = 600,
        poll_interval: int = 10,
        status_callback: Callable = None
    ) -> Dict[str, Any]:
        """
        Wait for gateway deployment to reach a target status.
        
        Polls the gateway status using show-simple-gateway and checks the
        deployment-status field until it reaches one of the target statuses.
        
        Args:
            gateway_name: Name of the gateway to monitor
            target_statuses: List of statuses to wait for (default: ["finished", "failed"])
            timeout: Maximum time to wait in seconds (default: 600 = 10 minutes)
            poll_interval: Time between status checks in seconds (default: 10)
            status_callback: Optional callback for status updates
            
        Returns:
            Final gateway status response
            
        Raises:
            TimeoutError: If timeout is reached
            httpx.HTTPError: If request fails
        """
        if target_statuses is None:
            target_statuses = ["finished", "failed", "trusted"]
        
        start_time = time.monotonic()
        last_status = None
        
        logger.info(f"Waiting for gateway '{gateway_name}' deployment status to reach: {target_statuses}")
        
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Timeout waiting for gateway deployment. Last status: {last_status}")
            
            try:
                gateway = await self.get_gateway(gateway_name)
                
                # Check various status fields that indicate deployment progress
                # The exact field depends on the gateway type and Smart-1 Cloud version
                sic_state = gateway.get("sic-state", "")
                trust_state = gateway.get("trust-state", "")
                # Also check for general status
                object_status = gateway.get("status", "")
                
                # Log current status
                current_status = {
                    "sic_state": sic_state,
                    "trust_state": trust_state,
                    "status": object_status
                }
                
                if current_status != last_status:
                    logger.info(f"Gateway '{gateway_name}' status: {current_status}")
                    last_status = current_status
                    
                    # Send status update via callback
                    if status_callback:
                        try:
                            result = status_callback({
                                "message": f"Deployment status: SIC={sic_state}, Trust={trust_state}",
                                "status": "in_progress",
                                "details": current_status
                            })
                            if inspect.isawaitable(result):
                                await result
                        except Exception as e:
                            logger.warning(f"Status callback failed: {e}")
                
                # Check if we've reached a target status
                # "trusted" in sic-state means SIC is established
                # "finished" or "completed" might appear in other fields
                for status in target_statuses:
                    status_lower = status.lower()
                    if (status_lower in sic_state.lower() or 
                        status_lower in trust_state.lower() or
                        status_lower in object_status.lower()):
                        logger.info(f"Gateway '{gateway_name}' reached target status: {status}")
                        return {
                            "success": True,
                            "gateway": gateway,
                            "final_status": current_status,
                            "reached_status": status
                        }
                
                # Check for failure conditions
                if "fail" in sic_state.lower() or "fail" in trust_state.lower():
                    logger.error(f"Gateway deployment failed: {current_status}")
                    return {
                        "success": False,
                        "gateway": gateway,
                        "final_status": current_status,
                        "error": "Deployment failed"
                    }
                
            except httpx.HTTPError as e:
                logger.warning(f"Error checking gateway status: {e}")
                # Continue polling on transient errors
            
            await asyncio.sleep(poll_interval)

    async def _relogin(self) -> None:
        """
        Re-authenticate when the session has expired (401).
        Replaces the current session_id with a fresh one.
        """
        logger.info("Session expired — re-logging in to Smart-1 Cloud...")
        self.session_id = None
        await self.login()
        logger.info("Re-login successful, resuming...")

    async def test_trust(self, gateway_name: str, trust_method: str = "cloud_token") -> Dict[str, Any]:
        """
        Call test-trust to check the current SIC/trust status of a gateway.
        Automatically re-logs in and retries once on 401 session-expired errors.

        Returns the full response including:
            sic-status, status (trust status), trust-details.authentication-token
        """
        return await self._api_call("test-trust", {"name": gateway_name})

    async def wait_for_cloud_token_communicating(
        self,
        gateway_name: str,
        timeout: int = 600,
        poll_interval: int = 10,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Poll test-trust until trust-details.status == "communicating".
        Used for Gaia gateways after set-trust (cloud_token) + publish, before setting the SIC OTP.
        The gateway has connected and the cloud token is active, but SIC is not yet initialized.

        Returns:
            Dict with success=True and the full trust response when communicating.
        """
        start_time = time.monotonic()
        last_status = None

        logger.info(f"Waiting for cloud-token 'communicating' on gateway '{gateway_name}' via test-trust...")

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"Timed out waiting for cloud-token communicating on '{gateway_name}' after {timeout}s")

            try:
                trust_response = await self.test_trust(gateway_name)
                trust_details = trust_response.get("trust-details", {})
                status = (trust_details.get("status") or "").lower()
                cloud_comm = (trust_details.get("cloud-communication-details", {}).get("status") or "").lower()

                if status != last_status:
                    logger.info(f"Gateway '{gateway_name}' cloud-token status={status}, cloud-comm={cloud_comm}")
                    last_status = status

                if status_callback:
                    try:
                        elapsed_min = int(elapsed) // 60
                        elapsed_sec = int(elapsed) % 60
                        cb_result = status_callback({
                            "message": f"Cloud-token status: {status} (elapsed {elapsed_min}m{elapsed_sec:02d}s)",
                            "status": "in_progress",
                            "details": {"trust_status": status, "cloud_comm": cloud_comm}
                        })
                        if inspect.isawaitable(cb_result):
                            await cb_result
                    except Exception as e:
                        logger.warning(f"Status callback failed: {e}")

                if status == "communicating" or cloud_comm == "up":
                    logger.info(f"Gateway '{gateway_name}' ready for SIC OTP (trust-status={status}, cloud-comm={cloud_comm})")
                    return {"success": True, "trust_status": status, "cloud_comm": cloud_comm, "response": trust_response}

                if status in {"failed", "revoked"}:
                    return {"success": False, "trust_status": status, "error": f"Cloud-token status: {status}"}

            except httpx.HTTPError as e:
                logger.warning(f"test-trust polling error (will retry): {e}")

            await asyncio.sleep(poll_interval)

    async def wait_for_sic_established(
        self,
        gateway_name: str,
        timeout: int = 600,
        poll_interval: int = 10,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Wait for SIC to be established on a gateway.
        Polls test-trust every poll_interval seconds until sic-status or status
        reaches a communicating/trusted state.
        
        Args:
            gateway_name: Gateway name to monitor
            timeout: Maximum time to wait in seconds (default: 600 = 10 minutes)
            poll_interval: Time between checks in seconds (default: 10)
            status_callback: Optional callback for status updates
            
        Returns:
            Dict with success status and final gateway state
        """
        # trust-details.status values from test-trust that indicate SIC is established
        success_states = {"initialized", "communicating", "trusted", "trust established"}
        # trust-details.status values that indicate permanent failure
        failure_states = {"failed"}

        start_time = time.monotonic()
        last_sic_status = None

        logger.info(f"Waiting for SIC establishment on gateway '{gateway_name}' via test-trust...")

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"SIC establishment timed out after {timeout} seconds")

            try:
                trust_response = await self.test_trust(gateway_name, trust_method="cloud_token")

                # REST API returns status inside trust-details (not top-level like mgmt_cli)
                trust_details = trust_response.get("trust-details", {})
                sic_status = (trust_details.get("status") or "").lower()
                cloud_comm_status = (trust_details.get("cloud-communication-details", {}).get("status") or "").lower()

                if sic_status != last_sic_status:
                    logger.info(f"Gateway '{gateway_name}' trust status={sic_status}, cloud-comm={cloud_comm_status}")
                    last_sic_status = sic_status

                # Always send status update so UI shows active polling
                if status_callback:
                    try:
                        elapsed_min = int(elapsed) // 60
                        elapsed_sec = int(elapsed) % 60
                        cb_result = status_callback({
                            "message": f"SIC status: {sic_status} (elapsed {elapsed_min}m{elapsed_sec:02d}s)",
                            "status": "in_progress",
                            "details": {
                                "sic_status": sic_status,
                                "cloud_comm_status": cloud_comm_status
                            }
                        })
                        if inspect.isawaitable(cb_result):
                            await cb_result
                    except Exception as e:
                        logger.warning(f"Status callback failed: {e}")

                # Success: trust status indicates connection is established
                if sic_status in success_states:
                    logger.info(f"SIC established on gateway '{gateway_name}': status={sic_status}")
                    return {
                        "success": True,
                        "sic_status": sic_status,
                        "message": f"SIC established: {sic_status}"
                    }

                # Permanent failure
                if sic_status in failure_states:
                    logger.error(f"SIC establishment failed: status={sic_status}")
                    return {
                        "success": False,
                        "sic_status": sic_status,
                        "error": f"SIC failed: {sic_status}"
                    }

                # All other states (waiting_for_first_connection, token_issued, unknown) → keep polling

            except httpx.HTTPError as e:
                logger.warning(f"test-trust polling error (will retry): {e}")
                # Continue polling on transient errors

            await asyncio.sleep(poll_interval)

    async def wait_for_sic_communicating(
        self,
        gateway_name: str,
        timeout: int = 600,
        poll_interval: int = 10,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Poll test-trust after SIC OTP has been published until sic-status == "communicating".
        Used for Gaia gateways after set-simple-gateway (one-time-password) + publish.

        Returns:
            Dict with success=True and sic_status when communicating.
        """
        start_time = time.monotonic()
        last_sic_status = None

        logger.info(f"Waiting for sic-status 'communicating' on gateway '{gateway_name}' via test-trust...")

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"Timed out waiting for sic-status communicating on '{gateway_name}' after {timeout}s")

            try:
                trust_response = await self.test_trust(gateway_name)
                trust_details = trust_response.get("trust-details", {})
                sic_status = (trust_response.get("sic-status") or "").lower()
                trust_status = (trust_details.get("status") or "").lower()
                cloud_comm = (trust_details.get("cloud-communication-details", {}).get("status") or "").lower()

                if sic_status != last_sic_status:
                    logger.info(f"Gateway '{gateway_name}' sic-status={sic_status}, trust-status={trust_status}, cloud-comm={cloud_comm}")
                    last_sic_status = sic_status

                if status_callback:
                    try:
                        elapsed_min = int(elapsed) // 60
                        elapsed_sec = int(elapsed) % 60
                        cb_result = status_callback({
                            "message": f"SIC status: {sic_status} (elapsed {elapsed_min}m{elapsed_sec:02d}s)",
                            "status": "in_progress",
                            "details": {"sic_status": sic_status, "trust_status": trust_status, "cloud_comm": cloud_comm}
                        })
                        if inspect.isawaitable(cb_result):
                            await cb_result
                    except Exception as e:
                        logger.warning(f"Status callback failed: {e}")

                if sic_status == "communicating":
                    logger.info(f"Gateway '{gateway_name}' SIC is communicating")
                    return {"success": True, "sic_status": sic_status, "response": trust_response}

                if sic_status in {"failed", "revoked"}:
                    return {"success": False, "sic_status": sic_status, "error": f"SIC failed: {sic_status}"}

            except httpx.HTTPError as e:
                logger.warning(f"test-trust polling error (will retry): {e}")

            await asyncio.sleep(poll_interval)

    async def get_interfaces(
        self,
        gateway_name: str,
        gateway_uid: Optional[str] = None,
        with_topology: bool = True,
        use_defined_by_routes: bool = True,
        group_interfaces_by_subnet: bool = True,
        allow_smb: bool = False,
        ignore_sic_status: bool = False
    ) -> Dict[str, Any]:
        """
        Get interfaces from a gateway with topology information.

        Args:
            gateway_name: Target gateway name (used as fallback if uid not provided)
            gateway_uid: Target gateway UID (preferred — use uid from show-simple-gateway response)
            with_topology: Fetch interfaces with their topology (default: True)
            use_defined_by_routes: Configure topology "Defined by Routes" where applicable (default: True)
            group_interfaces_by_subnet: Group interfaces by subnet (default: True)
            allow_smb: Set to True for Spark (SMB) gateways (default: False)

        Returns:
            Interfaces response with topology
        """
        url = f"{self.base_url}/web_api/get-interfaces"
        headers = self._get_headers()

        # Use target-uid when available (confirmed working), fall back to target-name
        if gateway_uid:
            payload = {
                "target-uid": gateway_uid,
                "with-topology": with_topology,
                "use-defined-by-routes": use_defined_by_routes,
                "group-interfaces-by-subnet": group_interfaces_by_subnet
            }
        else:
            payload = {
                "target-name": gateway_name,
                "with-topology": with_topology,
                "use-defined-by-routes": use_defined_by_routes,
                "group-interfaces-by-subnet": group_interfaces_by_subnet
            }

        if allow_smb:
            payload["allow-smb"] = True
        if ignore_sic_status:
            payload["ignore-sic-status"] = True

        max_retries = 5
        retry_delay = 15  # seconds between retries on 500

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Getting interfaces for gateway '{gateway_name}' (with_topology={with_topology}, attempt {attempt}/{max_retries})")
                log_http_request(logger.info, "get-interfaces", payload)

                response = await self.client.post(url, json=payload, headers=headers)

                log_http_response(logger.info, "get-interfaces", response.status_code, response.text)
                response.raise_for_status()

                result = response.json()
                task_id = result.get("task-id")

                if task_id:
                    logger.info(f"get-interfaces returned task-id: {task_id}, waiting for completion...")
                    task_result = await self.wait_for_task(task_id, timeout=120, poll_interval=5)
                    # Extract interfaces from task details
                    tasks = task_result.get("tasks", [])
                    if tasks:
                        task_details = tasks[0].get("task-details", [])
                        if task_details:
                            result = task_details[0]
                    interfaces = result.get("interfaces", [])
                    logger.info(f"Retrieved {len(interfaces)} interfaces for gateway '{gateway_name}'")
                    return result
                else:
                    interfaces = result.get("interfaces", [])
                    logger.info(f"Retrieved {len(interfaces)} interfaces for gateway '{gateway_name}'")
                    return result

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 500 and attempt < max_retries:
                    logger.warning(f"Get interfaces attempt {attempt} failed with 500, retrying in {retry_delay}s: {e.response.text}")
                    await asyncio.sleep(retry_delay)
                    continue
                logger.error(f"Get interfaces failed: {str(e)}")
                logger.error(f"get-interfaces response [{e.response.status_code}]: {e.response.text}")
                raise
            except httpx.HTTPError as e:
                logger.error(f"Get interfaces failed: {str(e)}")
                raise
