"""
SMP Deployment Orchestrator.
Handles Zero Touch-only deployment for SMP (Spark Management Portal) gateways.
No management server interaction — only Zero Touch claim, placeholder substitution, and activation.
"""
import httpx
from typing import Dict, Any, Optional, Callable
from loguru import logger
from app.services.zero_touch_service import ZeroTouchService
from app.services.script_utils import apply_script_substitutions
from app.services.callback_utils import call_callback as _call_callback


class SMPDeploymentOrchestrator:
    """Orchestrates SMP gateway deployment — Zero Touch only."""

    def __init__(self):
        self.zero_touch_service = ZeroTouchService()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.zero_touch_service.client.aclose()

    async def deploy_gateway(
        self,
        mac_address: str,
        account_id: str,
        template_id: Optional[str] = None,
        template_name: str = '',
        gateway_name: str = '',
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Deploy Spark gateway via SMP (Zero Touch only).

        Flow:
        1. Login to Zero Touch Portal
        2. Claim gateway to template (or verify already claimed)
        3. Get gateway configuration
        4. Replace placeholders in user-script (##!!, <gateway-name>)
        5. Update gateway configuration
        6. Remove under-construction flag

        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            template_id: Zero Touch template ID
            template_name: Template name
            gateway_name: Gateway name/hostname
            status_callback: Optional callback for status updates

        Returns:
            Deployment result
        """
        async def send_status(step: int, message: str, status: str = "in_progress", details: Optional[Dict] = None):
            status_info = {
                "step": step,
                "message": message,
                "status": status,
                "details": details or {}
            }
            logger.info(f"Step {step}: {message}")
            await _call_callback(status_callback, status_info)

        try:
            await send_status(0, f"Starting SMP deployment for gateway: {gateway_name} ({mac_address})")

            # Step 1: Login to Zero Touch Portal
            await send_status(1, "Logging in to Zero Touch Portal")
            zt_login = await self.zero_touch_service.login()
            if not zt_login.success:
                raise Exception(f"Zero Touch login failed: {zt_login.message}")

            # Step 2: Claim gateway (or verify already claimed)
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
                    logger.warning(f"Claim failed (may already be claimed): {claim_err}. Continuing...")
                    await send_status(2, f"Claim skipped (gateway may already be claimed)", status="warning")
            else:
                await send_status(2, "Skipping claim step (already claimed via UI)")

            # Step 3: Get gateway configuration
            await send_status(3, "Getting gateway configuration")
            config = await self.zero_touch_service.get_gateway_configuration(
                mac_address, account_id, template_name
            )
            user_script = config.get('user-script', '')
            logger.debug(f"Original user-script length: {len(user_script)}")

            # Step 4: Replace placeholders in user-script
            await send_status(4, "Replacing placeholders in user-script")
            user_script = apply_script_substitutions(user_script, gateway_name)

            # Step 5: Update gateway configuration
            await send_status(5, "Updating gateway configuration in Zero Touch Portal")
            await self.zero_touch_service.update_gateway_configuration(
                mac_address, account_id, template_name,
                {'user-script': user_script, 'under-construction': True}
            )
            logger.info("Gateway configuration updated successfully")

            # Step 6: Remove under-construction flag
            await send_status(6, "Removing under-construction flag")
            await self.zero_touch_service.update_gateway_configuration(
                mac_address, account_id, template_name,
                {'under-construction': False}
            )
            logger.info("Under-construction flag removed")

            await send_status(6, "SMP deployment completed successfully", "completed")

            return {
                "success": True,
                "gateway_name": gateway_name,
                "mac_address": mac_address,
            }

        except Exception as e:
            logger.error(f"SMP deployment failed: {str(e)}")
            logger.exception(e)
            raise Exception(f"SMP deployment failed: {str(e)}")
