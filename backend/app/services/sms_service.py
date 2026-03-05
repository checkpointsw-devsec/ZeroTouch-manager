"""
Service for managing Check Point SMS (Security Management Server) operations.
Handles simple gateway provisioning on Management Servers.
"""
import asyncio
import httpx
from typing import Dict, Any, Optional
from loguru import logger
from app.config import settings
from ..config import log_http_request, log_http_response


class SMSService:
    """Service for SMS gateway management operations."""

    def __init__(self, base_url: Optional[str] = None):
        """Initialize SMS service with management server configuration.

        Args:
            base_url: Optional management server base URL override (e.g. https://192.168.10.79/web_api).
                      If not provided, falls back to settings.mgmt_base_url.
        """
        # Use provided URL or fall back to settings, stripping trailing slashes
        if base_url:
            self.base_url = base_url.rstrip('/')
        else:
            self.base_url = settings.mgmt_base_url.rstrip('/')
        self.api_key = settings.mgmt_server_api_key
        # Increase timeout to 60 seconds for slower management servers
        self.client = httpx.AsyncClient(verify=settings.ssl_verify, timeout=60.0)
        self.session_id: Optional[str] = None

        logger.info(f"Initialized SMS Service")
        logger.info(f"Management URL: {self.base_url}")
        logger.debug(f"API Key: {self.api_key[:10]}..." if self.api_key else "API Key: NOT SET")

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - close HTTP client and logout."""
        if self.session_id:
            try:
                await self.logout()
            except Exception as e:
                logger.warning(f"Failed to logout: {e}")
        await self.client.aclose()

    async def login(self, domain: Optional[str] = None) -> str:
        """
        Login to management server using API key.

        Args:
            domain: Optional domain/CMA for Multi-Domain Server

        Returns:
            Session ID (sid)

        Raises:
            httpx.HTTPError: If login fails
        """
        try:
            logger.info("Logging in to Management Server with API key")

            url = f"{self.base_url}/login"
            headers = {
                "Content-Type": "application/json"
            }

            payload = {
                "api-key": self.api_key
            }

            if domain:
                payload["domain"] = domain
                logger.info(f"Using domain: {domain}")

            logger.info(f"POST {url}")
            logger.debug(f"API Key: {self.api_key[:10]}..." if self.api_key else "API Key: NOT SET")
            log_http_request(logger.info, "login", payload)
            response = await self.client.post(url, headers=headers, json=payload)

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "login", response.status_code, response.text)

            response.raise_for_status()

            data = response.json()
            self.session_id = data.get("sid")

            if not self.session_id:
                logger.error(f"Session ID not found in response: {data}")
                raise ValueError("Session ID not found in login response")

            logger.info(f"Successfully logged in. Session ID: {self.session_id[:10]}...")
            return self.session_id

        except httpx.TimeoutException as e:
            logger.error(f"Management server login timeout: Connection timed out after 60 seconds")
            logger.error(f"Please verify:")
            logger.error(f"  1. Management Server URL is correct: {self.base_url}")
            logger.error(f"  2. Management Server is accessible from this machine")
            logger.error(f"  3. Port 443 is open and not blocked by firewall")
            logger.error(f"  4. Management Server is running and responsive")
            raise Exception(f"Management server connection timeout. Please check server accessibility at {self.base_url}")
        except httpx.HTTPError as e:
            logger.error(f"Management server login failed: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise

    async def logout(self) -> Dict[str, Any]:
        """
        Logout from management server.

        Returns:
            Logout response
        """
        if not self.session_id:
            logger.warning("No active session to logout from")
            return {"message": "No active session"}

        try:
            logger.info("Logging out from Management Server")

            url = f"{self.base_url}/logout"
            headers = {
                "Content-Type": "application/json",
                "X-chkp-sid": self.session_id
            }

            logger.info(f"POST {url}")
            log_http_request(logger.info, "logout", {})
            response = await self.client.post(url, headers=headers, json={})

            logger.info(f"Logout response status: {response.status_code}")
            log_http_response(logger.info, "logout", response.status_code, response.text)

            response.raise_for_status()

            result = response.json()
            self.session_id = None

            logger.info("Successfully logged out")
            return result

        except httpx.HTTPError as e:
            logger.warning(f"Logout failed (non-critical): {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.warning(f"Response status: {e.response.status_code}")
                logger.warning(f"Response body: {e.response.text}")
            # Clear session even if logout failed
            self.session_id = None
            # Don't raise - logout failure shouldn't break the deployment
            return {"message": "Logout failed but continuing", "error": str(e)}

    async def add_simple_gateway(
        self,
        name: str,
        ipv4_address: str,
        sic_otp: str,
        version: str,
        hardware: str,
        is_spark: bool = False,
    ) -> Dict[str, Any]:
        """
        Add simple gateway to management server.

        Spark payload includes "allow-smb": true and omits it for Gaia.
        Both types send one-time-password so the SMS can establish SIC with the gateway.
        VPN community membership is set separately after blades are enabled and published.

        Args:
            name: Gateway name
            ipv4_address: Gateway IPv4 address
            sic_otp: SIC one-time password (must match the password in the gateway user-script)
            version: Gateway software version (e.g. R81.10)
            hardware: Gateway hardware model (e.g. Check Point 1590)
            is_spark: True for Spark gateways (adds allow-smb: true)

        Returns:
            Gateway creation response

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        try:
            logger.info(f"Adding simple gateway '{name}' to Management Server (is_spark={is_spark})")

            url = f"{self.base_url}/add-simple-gateway"
            headers = {
                "Content-Type": "application/json",
                "X-chkp-sid": self.session_id
            }

            payload = {
                "name": name,
                "version": version,
                "os-name": "Gaia Embedded" if is_spark else "Gaia",
                "hardware": hardware,
                "ipv4-address": ipv4_address,
                "firewall": True,
                "one-time-password": sic_otp,
            }

            if is_spark:
                payload["allow-smb"] = True
            # For Gaia, sic_otp is a dummy placeholder at creation time;
            # the real OTP is set later via set_sic_password() after ZT deployment finishes.

            logger.debug(f"POST {url}")
            log_http_request(logger.info, "add-simple-gateway", payload)
            response = await self.client.post(url, headers=headers, json=payload)

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "add-simple-gateway", response.status_code, response.text)

            response.raise_for_status()

            result = response.json()
            logger.info(f"Successfully added simple gateway '{name}'")

            return result

        except httpx.HTTPError as e:
            logger.error(f"Failed to add simple gateway: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")

                if e.response.status_code == 400:
                    try:
                        error_detail = e.response.json()
                        # Check top-level message and errors[] array
                        error_msg = error_detail.get('message', '')
                        errors = error_detail.get('errors', [])
                        all_messages = [error_msg] + [
                            err.get('message', '') if isinstance(err, dict) else str(err)
                            for err in errors
                        ]
                        combined = ' '.join(all_messages)
                        if 'More than one object named' in combined or 'already exists' in combined.lower():
                            raise Exception(
                                f"Gateway '{name}' already exists in Management Server. "
                                "Please delete the existing gateway object and try again."
                            )
                        # Surface the actual error messages
                        raise Exception(f"add-simple-gateway failed: {combined.strip()}")
                    except Exception as inner:
                        if 'add-simple-gateway failed' in str(inner) or 'already exists' in str(inner):
                            raise
                        pass
            raise

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
            url = f"{self.base_url}/show-vpn-community-star"
            headers = {
                "Content-Type": "application/json",
                "X-chkp-sid": self.session_id
            }

            log_http_request(logger.info, "show-vpn-community-star", {"name": community_name})
            response = await self.client.post(
                url,
                headers=headers,
                json={"name": community_name}
            )
            log_http_response(logger.info, "show-vpn-community-star", response.status_code, response.text)

            if response.status_code == 404:
                logger.warning(f"VPN community '{community_name}' not found")
                return {"message": "VPN community not found"}

            response.raise_for_status()
            community_data = response.json()

            # Update the community to add the gateway
            url = f"{self.base_url}/set-vpn-community-star"

            # Build the update payload based on role using the API's "add" syntax
            if role == "center":
                payload = {
                    "name": community_name,
                    "center-gateways": {
                        "add": gateway_name
                    }
                }
            else:
                payload = {
                    "name": community_name,
                    "satellite-gateways": {
                        "add": gateway_name
                    }
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
            raise

    async def show_task(self, task_id: str) -> Dict[str, Any]:
        """
        Show task progress and details.

        Args:
            task_id: Task unique identifier

        Returns:
            Task details including status and progress
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        try:
            url = f"{self.base_url}/show-task"
            headers = {
                "Content-Type": "application/json",
                "X-chkp-sid": self.session_id
            }

            payload = {"task-id": task_id}

            log_http_request(logger.info, "show-task", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "show-task", response.status_code, response.text)
            response.raise_for_status()

            result = response.json()
            return result

        except httpx.HTTPError as e:
            logger.error(f"Failed to get task status: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise

    async def publish(self) -> Dict[str, Any]:
        """
        Publish changes to management server and wait for task completion.

        Returns:
            Publish response with final task status
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        try:
            logger.info("Publishing changes to Management Server")

            url = f"{self.base_url}/publish"
            headers = {
                "Content-Type": "application/json",
                "X-chkp-sid": self.session_id
            }

            log_http_request(logger.info, "publish", {})
            response = await self.client.post(url, headers=headers, json={})

            logger.info(f"Response status: {response.status_code}")
            log_http_response(logger.info, "publish", response.status_code, response.text)

            response.raise_for_status()

            result = response.json()
            task_id = result.get("task-id")

            if task_id:
                logger.info(f"Publish task started: {task_id}")
                await self._wait_for_task(task_id, label="publish")
            else:
                logger.info("No task-id in publish response, assuming synchronous completion")

            return result

        except httpx.HTTPError as e:
            logger.error(f"Failed to publish: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise


    async def _wait_for_task(self, task_id: str, label: str = "task", max_wait_time: int = 300, wait_interval: int = 2) -> Dict[str, Any]:
        """
        Poll show-task until the task reaches a terminal state.

        Args:
            task_id: Task unique identifier
            label: Human-readable label for logging
            max_wait_time: Maximum seconds to wait (default: 300)
            wait_interval: Seconds between polls (default: 2)

        Returns:
            Final task dict

        Raises:
            Exception: If task fails or timeout reached
        """
        elapsed = 0
        while elapsed < max_wait_time:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval

            task_status = await self.show_task(task_id)
            tasks = task_status.get("tasks", [])
            if not tasks:
                continue

            task = tasks[0]
            status = task.get("status", "")
            progress = task.get("progress-percentage", 0)
            logger.info(f"{label} task status: {status} ({progress}%)")

            if status in ["succeeded", "succeeded with warnings", "partially succeeded"]:
                logger.info(f"{label} task completed successfully")
                return task
            elif status == "failed":
                error_msg = task.get("progress-description", "Unknown error")
                logger.error(f"{label} task failed: {error_msg}")
                raise Exception(f"{label} failed: {error_msg}")

        raise Exception(f"{label} task did not complete within {max_wait_time} seconds")

    async def test_trust(self, gateway_name: str) -> Dict[str, Any]:
        """
        Run test-trust to initiate SIC establishment on a gateway.

        API: POST /web_api/test-trust

        Args:
            gateway_name: Gateway name as it appears on the Management Server

        Returns:
            Response dict
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/test-trust"
        headers = {
            "Content-Type": "application/json",
            "X-chkp-sid": self.session_id
        }
        payload = {"name": gateway_name}

        logger.info(f"POST {url} (gateway={gateway_name})")
        log_http_request(logger.info, "test-trust", payload)
        response = await self.client.post(url, headers=headers, json=payload)
        log_http_response(logger.info, "test-trust", response.status_code, response.text)
        response.raise_for_status()

        return response.json()

    async def show_simple_gateway(self, gateway_name: str) -> Dict[str, Any]:
        """
        Show a simple gateway object and return its details.

        API: POST /web_api/show-simple-gateway
        The response includes 'sic-state' as a plain string reflecting current SIC status:
          uninitialized | initialized | communicating | not communicating |
          unknown | failed | waiting_for_first_connection

        Args:
            gateway_name: Gateway name

        Returns:
            Full gateway object dict including 'sic-state'
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/show-simple-gateway"
        headers = {
            "Content-Type": "application/json",
            "X-chkp-sid": self.session_id
        }
        payload = {"name": gateway_name}

        logger.info(f"POST {url} (gateway={gateway_name})")
        log_http_request(logger.info, "show-simple-gateway", payload)
        response = await self.client.post(url, headers=headers, json=payload)
        log_http_response(logger.info, "show-simple-gateway", response.status_code, response.text)
        response.raise_for_status()

        return response.json()

    async def wait_for_sic(
        self,
        gateway_name: str,
        max_wait_time: int = 300,
        poll_interval: int = 10
    ) -> Dict[str, Any]:
        """
        Poll test-trust every poll_interval seconds until sic-status is 'communicating'.

        Args:
            gateway_name: Gateway name
            max_wait_time: Maximum seconds to wait (default: 300)
            poll_interval: Seconds between test-trust calls (default: 10)

        Returns:
            Final test-trust response when sic-status = communicating

        Raises:
            Exception: If SIC is not established within max_wait_time
        """
        elapsed = 0
        last_state = None

        while elapsed < max_wait_time:
            # Wait before each call so the gateway has time to connect
            logger.info(f"Waiting {poll_interval}s before next test-trust check...")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                result = await self.test_trust(gateway_name)
                state = result.get("sic-status", "")

                if state != last_state:
                    logger.info(f"SIC status for '{gateway_name}': {state}")
                    last_state = state

                if state.lower() == "communicating":
                    logger.info(f"SIC established for '{gateway_name}'")
                    return result

            except Exception as e:
                logger.warning(f"test-trust poll failed (will retry): {e}")

        raise Exception(
            f"SIC not established for '{gateway_name}' after {max_wait_time} seconds. "
            f"Last sic-status: {last_state}"
        )

    async def get_interfaces(
        self,
        gateway_name: str,
        with_topology: bool = True,
        use_defined_by_routes: bool = True,
        ignore_sic_status: bool = False,
        allow_smb: bool = False,
    ) -> Dict[str, Any]:
        """
        Run get-interfaces on a gateway to retrieve and apply its interface topology.

        Args:
            gateway_name: Gateway name
            with_topology: Fetch interfaces with their topology (default: True)
            use_defined_by_routes: Configure topology "Defined by Routes" (default: True)
            ignore_sic_status: Skip SIC status check on the API side (default: False)
            allow_smb: Set to True for Spark (SMB) gateways (default: False)

        Returns:
            Response dict (may contain task-id for async completion)
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/get-interfaces"
        headers = {
            "Content-Type": "application/json",
            "X-chkp-sid": self.session_id
        }
        payload: Dict[str, Any] = {
            "target-name": gateway_name,
            "with-topology": with_topology,
            "use-defined-by-routes": use_defined_by_routes,
        }
        if ignore_sic_status:
            payload["ignore-sic-status"] = True
        if allow_smb:
            payload["allow-smb"] = True

        max_retries = 5
        retry_delay = 15  # seconds between retries on 500

        for attempt in range(1, max_retries + 1):
            logger.info(f"POST {url} (gateway={gateway_name}, attempt {attempt}/{max_retries})")
            log_http_request(logger.info, "get-interfaces", payload)
            try:
                response = await self.client.post(url, headers=headers, json=payload)
                log_http_response(logger.info, "get-interfaces", response.status_code, response.text)
                response.raise_for_status()

                result = response.json()

                # get-interfaces is async — wait for the returned task to finish
                task_id = result.get("task-id")
                if task_id:
                    logger.info(f"get-interfaces task started: {task_id}")
                    await self._wait_for_task(task_id, label="get-interfaces")

                return result

            except Exception as e:
                is_500 = hasattr(e, "response") and e.response is not None and e.response.status_code == 500
                if is_500 and attempt < max_retries:
                    logger.warning(f"get-interfaces attempt {attempt} failed with 500, retrying in {retry_delay}s: {e.response.text}")
                    await asyncio.sleep(retry_delay)
                    continue
                raise

    async def set_simple_gateway(
        self,
        gateway_name: str,
        enable_app_control: bool = True,
        enable_ips: bool = True,
        enable_url_filtering: bool = False,
        enable_content_awareness: bool = False,
        enable_ipsec: bool = True,
        enable_anti_bot: bool = True,
        enable_anti_virus: bool = True,
        enable_threat_emulation: bool = True
    ) -> Dict[str, Any]:
        """
        Update an existing simple gateway object to enable security blades.

        Args:
            gateway_name: Gateway name
            enable_app_control: Enable Application Control blade
            enable_ips: Enable IPS blade
            enable_url_filtering: Enable URL Filtering blade
            enable_content_awareness: Enable Content Awareness blade
            enable_ipsec: Enable IPsec VPN blade
            enable_anti_bot: Enable Anti-Bot blade
            enable_anti_virus: Enable Anti-Virus blade
            enable_threat_emulation: Enable Threat Emulation blade

        Returns:
            set-simple-gateway response
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/set-simple-gateway"
        headers = {
            "Content-Type": "application/json",
            "X-chkp-sid": self.session_id
        }
        payload: Dict[str, Any] = {
            "name": gateway_name,
            "application-control": enable_app_control,
            "ips": enable_ips,
            "url-filtering": enable_url_filtering,
            "content-awareness": enable_content_awareness,
            "vpn": enable_ipsec,
            "anti-bot": enable_anti_bot,
            "anti-virus": enable_anti_virus,
            "threat-emulation": enable_threat_emulation
        }

        logger.info(f"POST {url} (gateway={gateway_name}, blades={payload})")
        log_http_request(logger.info, "set-simple-gateway", payload)
        response = await self.client.post(url, headers=headers, json=payload)
        log_http_response(logger.info, "set-simple-gateway", response.status_code, response.text)
        response.raise_for_status()

        return response.json()

    async def set_sic_password(self, gateway_name: str, one_time_password: str) -> Dict[str, Any]:
        """
        Set the real SIC one-time-password on a gateway via set-simple-gateway.
        Used for Gaia gateways after Zero Touch deployment has finished and
        before test-trust polling begins.

        Args:
            gateway_name: Gateway name
            one_time_password: Real SIC OTP matching the gateway ftw-sic-key

        Returns:
            set-simple-gateway response
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/set-simple-gateway"
        headers = {
            "Content-Type": "application/json",
            "X-chkp-sid": self.session_id
        }
        payload: Dict[str, Any] = {
            "name": gateway_name,
            "one-time-password": one_time_password
        }

        logger.info(f"Setting real SIC OTP for gateway '{gateway_name}' via set-simple-gateway")
        log_http_request(logger.info, "set-sic-password", payload)
        response = await self.client.post(url, headers=headers, json=payload)
        log_http_response(logger.info, "set-sic-password", response.status_code, response.text)
        response.raise_for_status()

        logger.info(f"SIC OTP set successfully for '{gateway_name}'")
        return response.json()

    async def install_policy(
        self,
        policy_name: str,
        gateway_name: str,
        access: bool = True,
        threat_prevention: bool = True
    ) -> Dict[str, Any]:
        """
        Install a security policy on a gateway and wait for the task to complete.

        Args:
            policy_name: Policy package name (e.g. "Standard")
            gateway_name: Gateway name to install on
            access: Install access policy (default: True)
            threat_prevention: Install threat prevention policy (default: True)

        Returns:
            Final install-policy task result
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/install-policy"
        headers = {
            "Content-Type": "application/json",
            "X-chkp-sid": self.session_id
        }
        payload = {
            "policy-package": policy_name,
            "access": access,
            "threat-prevention": threat_prevention,
            "targets": [gateway_name],
        }

        logger.info(f"POST {url} (policy={policy_name}, gateway={gateway_name}, access={access}, threat-prevention={threat_prevention})")
        log_http_request(logger.info, "install-policy", payload)
        response = await self.client.post(url, headers=headers, json=payload)
        log_http_response(logger.info, "install-policy", response.status_code, response.text)
        response.raise_for_status()

        result = response.json()
        task_id = result.get("task-id")
        if task_id:
            logger.info(f"install-policy task started: {task_id}")
            await self._wait_for_task(task_id, label="install-policy", max_wait_time=600)

        return result

    async def show_gateway_capabilities(self, hardware: str = None, platform: str = None, version: str = None) -> Dict[str, Any]:
        """
        Fetch supported hardware, versions, and platforms from the Management Server.

        Calls: POST /web_api/show-gateway-capabilities

        Args:
            hardware: Optional hardware filter (e.g. "Check Point 1590")
            platform: Optional platform filter (e.g. "smb")
            version: Optional version filter (e.g. "R81.10")

        Returns:
            Dict with supported-hardware, supported-versions, supported-platforms etc.
        """
        if not self.session_id:
            raise ValueError("Not authenticated. Call login() first.")

        try:
            url = f"{self.base_url}/show-gateway-capabilities"
            headers = {
                "Content-Type": "application/json",
                "X-chkp-sid": self.session_id
            }

            payload: Dict[str, Any] = {}
            if hardware:
                payload["hardware"] = hardware
            if platform:
                payload["platform"] = platform
            if version:
                payload["version"] = version

            log_http_request(logger.info, "show-gateway-capabilities", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "show-gateway-capabilities", response.status_code, response.text)
            response.raise_for_status()

            return response.json()

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch gateway capabilities: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise


async def get_sms_service() -> SMSService:
    """Dependency injection function for SMS service."""
    return SMSService()
