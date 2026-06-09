"""
LSM Deployment Orchestrator.
Handles the complete deployment flow for LSM (Large Scale Management) gateways.
"""
import httpx
from typing import Dict, Any, Optional, Callable
from loguru import logger
from app.services.zero_touch_service import ZeroTouchService
from app.services.lsm_service import LSMService
from app.services.script_utils import apply_script_substitutions
from app.services.callback_utils import call_callback as _call_callback
from app.config import settings


class LSMDeploymentOrchestrator:
    """Orchestrates LSM gateway deployment across Zero Touch Portal and Management Server."""

    def __init__(self):
        """Initialize the LSM deployment orchestrator."""
        self.zero_touch_service = ZeroTouchService()
        self.lsm_service = LSMService()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.lsm_service.session_id:
            try:
                await self.lsm_service.logout()
            except Exception as e:
                logger.warning(f"Failed to logout LSM during cleanup: {e}")
        await self.lsm_service.client.aclose()
        await self.zero_touch_service.client.aclose()

    async def deploy_gateway(
        self,
        mac_address: str,
        account_id: str,
        template_name: str,
        gateway_name: str,
        template_id: Optional[str] = None,
        mgmt_server_ip: Optional[str] = None,
        sic_otp: Optional[str] = None,
        security_profile: Optional[str] = None,
        provisioning_profile: Optional[str] = None,
        domain: Optional[str] = None,
        gateway_ipv4: Optional[str] = None,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Deploy Spark gateway to LSM (Large Scale Management).

        Flow:
        1. Login to Zero Touch Portal
        2. Claim gateway in Zero Touch (if template_id provided)
        3. Get gateway configuration
        4. Replace placeholders in user-script (<gateway-name>, <mgmt-server-ip>, <sic-key>)
        5. Update gateway configuration
        6. Login to Management Server
        7. Add LSM gateway
        8. Publish changes
        9. Remove under-construction flag

        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            template_name: Template name
            gateway_name: Gateway name
            template_id: Zero Touch template ID (required to claim unclaimed gateways)
            mgmt_server_ip: Management Server IP (resolved from user-script if not provided)
            sic_otp: SIC one-time password (resolved from user-script if not provided)
            security_profile: LSM security profile name
            provisioning_profile: LSM provisioning profile name
            domain: Optional domain for Multi-Domain Server
            status_callback: Optional callback for status updates

        Returns:
            Deployment result
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
            await send_status(0, f"Starting LSM deployment for gateway: {gateway_name} ({mac_address})")

            # Step 1: Login to Zero Touch Portal
            await send_status(1, "Logging in to Zero Touch Portal")
            zt_login = await self.zero_touch_service.login()
            if not zt_login.success:
                raise Exception(f"Zero Touch login failed: {zt_login.message}")

            # Step 2: Claim gateway in Zero Touch (if template_id provided)
            if template_id:
                await send_status(2, f"Claiming gateway {gateway_name} in Zero Touch (template: {template_name})...")
                try:
                    await self.zero_touch_service.claim_gateway(
                        mac_address=mac_address,
                        gateway_name=gateway_name,
                        template_id=template_id,
                        account_id=account_id,
                        custom_settings={"under_construction": True}
                    )
                    logger.info(f"Gateway '{gateway_name}' claimed in Zero Touch")
                except Exception as claim_err:
                    err_msg = str(claim_err)
                    # "already exists" means the gateway was previously claimed — safe to continue
                    if "already exists" in err_msg:
                        logger.warning(f"Claim skipped for {gateway_name} (already claimed): {err_msg}")
                        await send_status(2, "Claim skipped (gateway already claimed)", status="warning")
                    else:
                        logger.error(f"Claim failed for {gateway_name}: {claim_err}")
                        raise Exception(f"Failed to claim gateway '{gateway_name}' in Zero Touch: {claim_err}")
            else:
                await send_status(2, "Skipping claim step (no template_id provided)")

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

            # Resolve mgmt_server_ip: request > settings
            resolved_mgmt_ip = mgmt_server_ip or settings.mgmt_server_host
            if not resolved_mgmt_ip:
                raise Exception("mgmt_server_ip not provided and MGMT_SERVER_HOST is not set in configuration")

            # Replace Management Server IP
            if '<mgmt-server-ip>' in user_script:
                user_script = user_script.replace("<mgmt-server-ip>", resolved_mgmt_ip)
                logger.debug(f"Replaced <mgmt-server-ip> with {resolved_mgmt_ip}")

            # Replace SIC key placeholder if sic_otp provided
            if sic_otp and '<sic-key>' in user_script:
                user_script = user_script.replace("<sic-key>", sic_otp)
                logger.debug("Replaced <sic-key> placeholder")

            # Extract resolved SIC key from user-script (post-substitution)
            resolved_sic_key = sic_otp
            for line in user_script.splitlines():
                stripped = line.strip()
                if not resolved_sic_key and stripped.startswith("set sic_init password "):
                    resolved_sic_key = stripped[len("set sic_init password "):]
                    logger.debug(f"Resolved SIC key from user-script: {resolved_sic_key}")
                    break

            if not resolved_sic_key:
                raise Exception("sic_otp not provided and could not be resolved from user-script")

            # Step 5: Update gateway configuration
            await send_status(5, "Updating gateway configuration in Zero Touch Portal")
            zt_update_payload = {'user-script': user_script, 'under-construction': True}
            if gateway_ipv4:
                zt_update_payload['mgmt-eth-ip-address-ipv4'] = gateway_ipv4
                logger.debug(f"Set mgmt-eth-ip-address-ipv4 to {gateway_ipv4} in ZT update")
            await self.zero_touch_service.update_gateway_configuration(
                mac_address, account_id, template_name, zt_update_payload
            )
            logger.info("Gateway configuration updated successfully")

            # Step 6: Login to Management Server
            await send_status(6, "Logging in to Management Server")
            await self.lsm_service.login(domain=domain)

            # Step 7: Add LSM gateway
            await send_status(7, "Adding LSM gateway to Management Server")
            gateway_result = await self.lsm_service.add_lsm_gateway(
                name=gateway_name,
                sic_otp=resolved_sic_key,
                security_profile=security_profile or "",
                provisioning_profile=provisioning_profile or ""
            )
            logger.info("LSM gateway added successfully")

            # Step 8: Publish changes
            await send_status(8, "Publishing changes")
            await self.lsm_service.publish()
            logger.info("Changes published successfully")

            # Step 9: Remove under-construction flag
            await send_status(9, "Removing under-construction flag")
            await self.zero_touch_service.update_gateway_configuration(
                mac_address, account_id, template_name, {'under-construction': False}
            )
            logger.info("Under-construction flag removed")

            # Step 10: Logout from Management Server
            await send_status(10, "Logging out from Management Server")
            try:
                await self.lsm_service.logout()
            except Exception as logout_error:
                logger.warning(f"Logout failed (non-critical): {logout_error}")

            await send_status(10, "LSM deployment completed successfully", "completed")

            return {
                "success": True,
                "gateway_name": gateway_name,
                "mac_address": mac_address,
                "gateway_result": gateway_result
            }

        except httpx.TimeoutException as e:
            logger.error(f"LSM deployment failed: Management Server connection timeout")
            error_msg = (
                "Failed to connect to Management Server. "
                "Please verify the server is accessible and responding. "
                f"URL: {self.lsm_service.base_url}"
            )
            raise Exception(error_msg)

        except Exception as e:
            logger.error(f"LSM deployment failed: {str(e)}")
            logger.exception(e)
            raise Exception(f"LSM deployment failed: {str(e)}")


async def get_lsm_deployment_orchestrator() -> LSMDeploymentOrchestrator:
    """Dependency injection function for LSM deployment orchestrator."""
    return LSMDeploymentOrchestrator()
