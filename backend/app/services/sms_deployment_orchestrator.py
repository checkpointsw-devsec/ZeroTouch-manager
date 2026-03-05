"""
SMS Deployment Orchestrator.
Handles the complete deployment flow for SMS (Security Management Server) gateways.
"""
import asyncio
import httpx
from typing import Dict, Any, Optional, Callable, AsyncGenerator, Union
from loguru import logger
from app.services.zero_touch_service import ZeroTouchService
from app.services.sms_service import SMSService
from app.services.script_utils import apply_script_substitutions
from app.services.callback_utils import call_callback as _call_callback
from app.config import log_http_request, log_http_response, settings

# Valid deployment statuses
DEPLOYMENT_STATUS_FINISHED = "Finished"
DEPLOYMENT_STATUS_FAILED = "Failed"
DEPLOYMENT_STATUS_ERROR = "Error"
DEPLOYMENT_TERMINAL_STATUSES = {DEPLOYMENT_STATUS_FINISHED, DEPLOYMENT_STATUS_FAILED, DEPLOYMENT_STATUS_ERROR}

# Spark gateways report "Not available" from show-claimed-gateway-status because
# they do not push status updates back to Zero Touch. This is normal — it means
# the gateway has downloaded its configuration and is running the user-script.
SPARK_TERMINAL_STATUSES = {DEPLOYMENT_STATUS_FINISHED, DEPLOYMENT_STATUS_FAILED, DEPLOYMENT_STATUS_ERROR, "Not available"}


class SMSDeploymentOrchestrator:
    """Orchestrates SMS gateway deployment across Zero Touch Portal and Management Server."""

    def __init__(self, mgmt_base_url: Optional[str] = None):
        """Initialize the SMS deployment orchestrator.

        Args:
            mgmt_base_url: Optional management server base URL override
                           (e.g. https://192.168.10.79/web_api).
        """
        self.zero_touch_service = ZeroTouchService()
        self.sms_service = SMSService(base_url=mgmt_base_url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.sms_service.session_id:
            try:
                await self.sms_service.logout()
            except Exception as e:
                logger.warning(f"Failed to logout SMS during cleanup: {e}")
        await self.sms_service.client.aclose()
        await self.zero_touch_service.client.aclose()

    async def deploy_gateway(
        self,
        mac_address: str,
        account_id: str,
        template_name: str,
        gateway_name: str,
        mgmt_server_ip: str,
        sic_otp: str,
        gateway_ipv4: str,
        version: str,
        hardware: str,
        template_id: Optional[str] = None,
        policy_name: str = "Standard",
        enable_app_control: bool = True,
        enable_ips: bool = True,
        enable_url_filtering: bool = False,
        enable_content_awareness: bool = False,
        enable_ipsec: bool = True,
        enable_anti_bot: bool = True,
        enable_anti_virus: bool = True,
        enable_threat_emulation: bool = True,
        vpn_community: Optional[str] = None,
        vpn_role: str = "satellite",
        domain: Optional[str] = None,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Deploy gateway to SMS (Security Management Server).

        Flow:
        1. Login to Zero Touch Portal
        2. Claim gateway if not already claimed (requires template_id)
        3. Get gateway configuration
        4. Replace placeholders in user-script (<mgmt-server-ip>, <sic-key> for Spark)
        5. Update gateway configuration (set under-construction=false to start deployment)
        6. Login to Management Server
        7. Add simple gateway object
        8. Publish changes (gateway object)
        9. Wait for SIC to be established (test-trust until status = communicating)
        10. Get interfaces + publish + wait for task
        11. Enable security blades (set-simple-gateway) + publish + wait for task
        12. Install policy + wait for task
        13. Get activation link (Gaia only)

        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            template_id: Zero Touch template ID (required to claim unclaimed gateways)
            template_name: Template name
            gateway_name: Gateway name
            mgmt_server_ip: Management Server IP address
            sic_otp: SIC one-time password
            gateway_ipv4: Gateway IPv4 address for the management server object
            version: Gateway software version (e.g. R81.10)
            hardware: Gateway hardware model (e.g. Check Point 1590)
            policy_name: Security policy name (default: "Standard")
            enable_app_control: Enable Application Control blade
            enable_ips: Enable IPS blade
            enable_url_filtering: Enable URL Filtering blade
            enable_content_awareness: Enable Content Awareness blade
            enable_ipsec: Enable IPsec VPN blade
            vpn_community: Optional VPN community name
            vpn_role: Gateway role in VPN community - "center" or "satellite"
            domain: Optional domain for Multi-Domain Server
            status_callback: Optional callback function to receive status updates

        Returns:
            Deployment result with activation link (if applicable)
        """
        async def send_status(step: int, message: str, status: str = "in_progress", details: Optional[Dict] = None):
            """Send status update via callback if provided"""
            status_info = {
                "step": step,
                "message": message,
                "status": status,
                "details": details or {}
            }
            logger.info(f"Step {step}: {message}")
            await _call_callback(status_callback, status_info)

        try:
            await send_status(0, f"Starting SMS deployment for gateway: {gateway_name} ({mac_address})")

            # Determine gateway type (Spark vs Gaia)
            is_spark = template_name and 'spark' in template_name.lower()
            logger.info(f"Gateway type: {'Spark' if is_spark else 'Gaia'}")

            # Step 1: Login to Zero Touch Portal
            await send_status(1, "Logging in to Zero Touch Portal")
            zt_login = await self.zero_touch_service.login()
            if not zt_login.success:
                raise Exception(f"Zero Touch login failed: {zt_login.message}")

            # Step 2: Claim gateway if not already claimed
            await send_status(2, "Checking if gateway is claimed in Zero Touch")
            try:
                await self.zero_touch_service.get_gateway_configuration(
                    mac_address, account_id, template_name
                )
                logger.info("Gateway already claimed in Zero Touch — skipping claim")
            except Exception as e:
                err_str = str(e).lower()
                if "does not exist" in err_str or "does not exists" in err_str:
                    # Gateway not claimed yet — claim it now
                    if not template_id:
                        raise Exception(
                            f"Gateway {mac_address} is not claimed in Zero Touch and no "
                            "template_id was provided. Add template_id to the CSV to auto-claim."
                        )
                    await send_status(2, f"Gateway not claimed — claiming with template {template_id}")
                    claim_result = await self.zero_touch_service.claim_gateway(
                        mac_address=mac_address,
                        gateway_name=gateway_name,
                        template_id=template_id,
                        account_id=account_id
                    )
                    logger.info(f"Gateway claimed successfully: {claim_result.message}")
                else:
                    raise

            # Step 3: Get current gateway configuration
            await send_status(3, "Getting gateway configuration")
            config = await self.zero_touch_service.get_gateway_configuration(
                mac_address, account_id, template_name
            )

            # Extract user-script
            user_script = config.get('user-script', '')
            logger.debug(f"Original user-script length: {len(user_script)}")

            # Step 4: Replace placeholders in user-script
            await send_status(4, "Replacing placeholders in user-script")

            # Apply ##!! file injection and <gateway-name> substitution
            user_script = apply_script_substitutions(user_script, gateway_name)

            # Replace Management Server IP (for both Spark and Gaia)
            if '<mgmt-server-ip>' in user_script:
                user_script = user_script.replace("<mgmt-server-ip>", mgmt_server_ip)
                logger.debug(f"Replaced <mgmt-server-ip> with {mgmt_server_ip}")

            # Replace SIC key (Spark only)
            if is_spark:
                if '<sic-key>' in user_script:
                    user_script = user_script.replace("<sic-key>", sic_otp)
                    logger.debug("Replaced <sic-key> placeholder (Spark gateway)")
            else:
                logger.debug("Skipping <sic-key> replacement (Gaia gateway - will use ftw-sic-key API field)")

            # Step 5: Update gateway configuration and start deployment
            await send_status(5, "Updating gateway configuration and starting deployment")
            update_payload = {
                'user-script': user_script,
                'under-construction': False  # Set to False to start deployment
            }

            # For Gaia gateways, set ftw-sic-key and override management IP
            if not is_spark:
                update_payload['ftw-sic-key'] = sic_otp
                logger.debug("Added ftw-sic-key to payload (Gaia gateway)")
                if gateway_ipv4:
                    update_payload['mgmt-eth-ip-address-ipv4'] = gateway_ipv4
                    logger.debug(f"Set mgmt-eth-ip-address-ipv4 to {gateway_ipv4} (Gaia gateway)")

            await self.zero_touch_service.update_gateway_configuration(
                mac_address, account_id, template_name, update_payload
            )
            logger.info("Gateway configuration updated - deployment started")

            # Step 6: Login to Management Server
            await send_status(6, "Logging in to Management Server")
            await self.sms_service.login(domain=domain)

            # Step 7: Add simple gateway
            # Spark: use real OTP (gateway initiates SIC via user-script)
            # Gaia:  use dummy OTP now; real OTP set after ZT deployment finishes
            await send_status(7, "Adding simple gateway to Management Server")
            dummy_otp = "DummyOTP123" if not is_spark else sic_otp
            gateway_result = await self.sms_service.add_simple_gateway(
                name=gateway_name,
                ipv4_address=gateway_ipv4,
                sic_otp=dummy_otp,
                version=version,
                hardware=hardware,
                is_spark=is_spark,
            )
            logger.info("Simple gateway added successfully")

            # Step 8: Publish changes (gateway object)
            await send_status(8, "Publishing gateway object to Management Server")
            await self.sms_service.publish()
            logger.info("Gateway object published successfully")

            if not is_spark:
                # ── Gaia-only flow ────────────────────────────────────────────
                # Step 9: Wait for Zero Touch to report deployment status = "Finished"
                await send_status(9, "Waiting for Zero Touch deployment to finish (show-gaia-claimed-gateway-status)")
                zt_max_wait = 900
                zt_poll = 20
                zt_elapsed = 0
                zt_last_status = None
                while zt_elapsed < zt_max_wait:
                    await asyncio.sleep(zt_poll)
                    zt_elapsed += zt_poll
                    try:
                        zt_status = await self.zero_touch_service.get_gateway_deployment_status(
                            mac_address=mac_address,
                            account_id=account_id,
                            is_spark=False
                        )
                        reported = zt_status.get("reported-display-status", "")
                        if reported != zt_last_status:
                            logger.info(f"Zero Touch deployment status: {reported}")
                            await send_status(9, f"Zero Touch deployment status: {reported}")
                            zt_last_status = reported
                        if reported.lower() == "finished":
                            logger.info("Zero Touch deployment finished")
                            break
                        if reported.lower() in ("failed", "error"):
                            raise Exception(f"Zero Touch deployment failed with status: {reported}")
                    except Exception as e:
                        logger.warning(f"ZT status poll failed (will retry): {e}")
                else:
                    raise Exception(f"Zero Touch deployment did not finish within {zt_max_wait}s (last status: {zt_last_status})")

                # Step 10: Set real SIC OTP + publish
                await send_status(10, "Setting real SIC one-time-password on gateway")
                await self.sms_service.set_sic_password(
                    gateway_name=gateway_name,
                    one_time_password=sic_otp
                )
                await send_status(10, "Publishing SIC OTP change")
                await self.sms_service.publish()
                logger.info("SIC OTP set and published")

                # Step 11: Wait for SIC to be established
                await send_status(11, "Waiting for SIC trust (polling test-trust every 10s)")
                await self.sms_service.wait_for_sic(
                    gateway_name=gateway_name,
                    max_wait_time=settings.sic_timeout,
                    poll_interval=10
                )
                logger.info("SIC established — gateway is communicating")

            else:
                # ── Spark-only flow ───────────────────────────────────────────
                # Step 9: Wait for SIC (Spark initiates SIC via user-script, no ZT polling needed)
                await send_status(9, "Waiting for SIC trust (polling test-trust every 10s)")
                await self.sms_service.wait_for_sic(
                    gateway_name=gateway_name,
                    max_wait_time=settings.sic_timeout,
                    poll_interval=10
                )
                logger.info("SIC established — gateway is communicating")

            # Step 12 (was 10): Get interfaces + publish
            await send_status(12, "Retrieving gateway interfaces and topology")
            await self.sms_service.get_interfaces(
                gateway_name=gateway_name,
                ignore_sic_status=True,
                allow_smb=is_spark,   # only True for Spark
            )
            logger.info("Interfaces retrieved — publishing")
            await send_status(12, "Publishing interface topology")
            await self.sms_service.publish()
            logger.info("Interface topology published")

            # Step 13 (was 11): Enable security blades + publish
            await send_status(13, "Enabling security blades on gateway")
            await self.sms_service.set_simple_gateway(
                gateway_name=gateway_name,
                enable_app_control=enable_app_control,
                enable_ips=enable_ips,
                enable_url_filtering=enable_url_filtering,
                enable_content_awareness=enable_content_awareness,
                enable_ipsec=enable_ipsec,
                enable_anti_bot=enable_anti_bot,
                enable_anti_virus=enable_anti_virus,
                enable_threat_emulation=enable_threat_emulation
            )
            logger.info("Security blades configured — publishing")
            await send_status(13, "Publishing blade configuration")
            await self.sms_service.publish()
            logger.info("Blade configuration published")

            # Step 14: Add gateway to VPN community (VPN blade must be enabled and published first)
            if vpn_community and enable_ipsec:
                await send_status(14, f"Adding gateway to VPN community '{vpn_community}' as {vpn_role}")
                try:
                    await self.sms_service.add_gateway_to_vpn_community(
                        gateway_name=gateway_name,
                        community_name=vpn_community,
                        role=vpn_role
                    )
                    logger.info(f"Added gateway to VPN community '{vpn_community}' as {vpn_role}")
                    await send_status(14, "Publishing VPN community changes")
                    await self.sms_service.publish()
                    logger.info("VPN community changes published")
                except Exception as e:
                    logger.warning(f"Failed to add gateway to VPN community: {e}")

            # Step 15: Install access policy first, then access + threat-prevention
            await send_status(15, f"Installing access policy '{policy_name}' on gateway")
            await self.sms_service.install_policy(
                policy_name=policy_name,
                gateway_name=gateway_name,
                access=True,
                threat_prevention=False
            )
            logger.info(f"Access policy '{policy_name}' installed successfully")

            await send_status(15, f"Installing threat prevention policy '{policy_name}' on gateway")
            await self.sms_service.install_policy(
                policy_name=policy_name,
                gateway_name=gateway_name,
                access=True,
                threat_prevention=True
            )
            logger.info(f"Threat prevention policy '{policy_name}' installed successfully")

            # Step 16: Get activation link (Gaia only)
            activation_link = None
            if not is_spark:
                await send_status(16, "Getting activation link (Gaia gateway)")
                try:
                    endpoint = '/show-gaia-claimed-gateway'
                    url = f"{self.zero_touch_service.base_url}{endpoint}"
                    headers = self.zero_touch_service._get_headers()
                    payload = {
                        'account-id': int(account_id),
                        'mac': mac_address.upper()
                    }

                    log_http_request(logger.debug, "show-gaia-claimed-gateway", payload)
                    response = await self.zero_touch_service.client.post(
                        url, headers=headers, json=payload
                    )
                    log_http_response(logger.debug, "show-gaia-claimed-gateway", response)
                    response.raise_for_status()
                    gateway_data = response.json()

                    activation_url_key = gateway_data.get('activation-url-key')
                    if activation_url_key:
                        activation_link = f"https://zerotouch.checkpoint.com/ZeroTouch/activatelink/{activation_url_key}"
                        logger.info(f"Activation link: {activation_link}")
                    else:
                        logger.warning("No activation-url-key found in gateway data")

                except Exception as e:
                    logger.warning(f"Failed to get activation link: {e}")
            else:
                await send_status(16, "Skipping activation link (Spark gateway)")

            # Step 17: Logout from Management Server
            await send_status(17, "Logging out from Management Server")
            try:
                await self.sms_service.logout()
            except Exception as logout_error:
                logger.warning(f"Logout failed (non-critical): {logout_error}")

            await send_status(17, "SMS deployment completed successfully", "completed")

            return {
                "success": True,
                "gateway_name": gateway_name,
                "mac_address": mac_address,
                "gateway_ipv4": gateway_ipv4,
                "activation_link": activation_link,
                "gateway_result": gateway_result,
                "policy_name": policy_name,
                "vpn_community": vpn_community,
                "vpn_role": vpn_role if vpn_community else None
            }

        except httpx.TimeoutException as e:
            logger.error(f"SMS deployment failed: Management Server connection timeout")
            error_msg = (
                "Failed to connect to Management Server. "
                "Please verify the server is accessible and responding. "
                f"URL: {self.sms_service.base_url}"
            )
            raise Exception(error_msg)

        except Exception as e:
            logger.error(f"SMS deployment failed: {str(e)}")
            logger.exception(e)
            raise Exception(f"SMS deployment failed: {str(e)}")

    async def _wait_for_deployment_status(
        self,
        mac_address: str,
        account_id: str,
        is_spark: bool = False,
        polling_interval: int = 60,
        max_wait_time: int = 3600,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Poll gateway deployment status until it reaches a terminal state.
        
        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            polling_interval: Seconds between status checks (default: 60)
            max_wait_time: Maximum seconds to wait (default: 3600 = 1 hour)
            status_callback: Optional callback for status updates
            
        Returns:
            Final status response from Zero Touch Portal
            
        Raises:
            Exception: If max wait time exceeded or deployment failed
        """
        elapsed_time = 0
        last_status = None
        
        while elapsed_time < max_wait_time:
            try:
                status = await self.zero_touch_service.get_gateway_deployment_status(
                    mac_address=mac_address,
                    account_id=account_id,
                    is_spark=is_spark
                )
                
                current_status = status.get('reported-display-status', 'Unknown')
                reported_time = status.get('reported-status-time')
                
                # Notify on status change or first status
                if current_status != last_status:
                    logger.info(f"Deployment status changed: {last_status} -> {current_status}")
                    last_status = current_status
                    # Add step info to the status for frontend
                    status_with_step = {
                        'step': 6,
                        'message': f"Waiting for deployment: {current_status}",
                        'status': 'in_progress',
                        'details': status
                    }
                    logger.info(f"Sending status callback: {status_with_step}")
                    if status_callback:
                        await _call_callback(status_callback, status_with_step)
                
                # Check for terminal status
                terminal_statuses = SPARK_TERMINAL_STATUSES if is_spark else DEPLOYMENT_TERMINAL_STATUSES
                if current_status in terminal_statuses:
                    # Spark returns "Not available" when it has no status reporting — treat as Finished
                    if is_spark and current_status == "Not available":
                        logger.info("Spark gateway reports 'Not available' — gateway has downloaded config, treating as Finished")
                        status['reported-display-status'] = DEPLOYMENT_STATUS_FINISHED
                    else:
                        logger.info(f"Deployment reached terminal status: {current_status}")
                    return status
                
                # Log progress
                logger.info(f"Deployment in progress - Status: {current_status}, Elapsed: {elapsed_time}s")
                
            except Exception as e:
                logger.warning(f"Failed to get deployment status (will retry): {e}")
            
            # Wait before next poll
            await asyncio.sleep(polling_interval)
            elapsed_time += polling_interval
        
        # Timeout reached
        raise Exception(
            f"Deployment status check timed out after {max_wait_time} seconds. "
            f"Last status: {last_status}"
        )


async def get_sms_deployment_orchestrator() -> SMSDeploymentOrchestrator:
    """Dependency injection function for SMS deployment orchestrator."""
    return SMSDeploymentOrchestrator()
