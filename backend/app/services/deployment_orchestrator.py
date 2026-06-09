"""
Orchestration service for complete gateway deployment flow.
Combines Zero Touch Portal and Smart-1 Cloud operations.
"""
import re
import asyncio
from typing import Dict, Any, Optional, Callable
from loguru import logger

from .zero_touch_service import ZeroTouchService
from .smart1_cloud_service import Smart1CloudService
from .callback_utils import call_callback as _call_callback
from .script_utils import apply_script_substitutions
from ..config import log_http_request, log_http_response, settings


class GatewayDeploymentOrchestrator:
    """
    Orchestrates the complete gateway deployment workflow.
    Integrates Zero Touch Portal and Smart-1 Cloud operations.
    """
    
    def __init__(self):
        self.zero_touch = ZeroTouchService()
        self.smart1_cloud = Smart1CloudService()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.zero_touch.client.aclose()
        await self.smart1_cloud.client.aclose()
    
    async def deploy_gateway_with_smart1_cloud(
        self,
        # Zero Touch parameters
        mac_address: str,
        account_id: str,
        template_id: Optional[str],
        template_name: str,
        gateway_name: str,
        user_script: str,
        time_zone: str,
        # Smart-1 Cloud parameters
        sic_otp: str,
        hardware: Optional[str] = None,
        gateway_type: str = "APPLIANCE_OR_OPENSERVER",
        identification_method: str = "GATEWAY_NAME",
        # OS version (for Spark gateways)
        os_version: Optional[str] = None,
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
        vpn_role: str = "satellite",
        # Policy installation
        policy_name: Optional[str] = None,
        # IPv4 address (Gaia only — used instead of auto-generate-ip)
        ipv4_address: Optional[str] = None,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Complete deployment flow:

        Spark gateways:
        1.  Authenticate with Zero Touch and Smart-1 Cloud
        2.  Claim gateway in Zero Touch (mac + name + template_id + account_id, under-construction=True)
        3a. add-simple-gateway (name + auto-generate-ip)
        3b. set-simple-gateway (version, hardware, allow-smb=True)
        3c. set-trust OTP (one-time-password, when_gateway_connects, allow-smb=True)
        3d. set-trust cloud_token (allow-smb=True) → get MaaS token
        3e. publish → wait for task
        3f. show-simple-gateway → get WAN IP
        4.  Update Zero Touch user-script with MaaS token + SIC key, remove under-construction
        5.  Configure VPN interface (if VPN community specified)
        11. wait_for_sic_established (test-trust, sic-status == communicating)
        12. get-interfaces → publish → wait for task
        13-16. VPN community, policy install

        Gaia gateways:
        1.  Authenticate with Zero Touch and Smart-1 Cloud
        2.  Claim gateway in Zero Touch (mac + name + template_id + account_id, under-construction=True)
        3a. add-simple-gateway (name + auto-generate-ip only — no OTP, no trust-method)
        3b. set-simple-gateway (version, hardware — no allow-smb)
        3c. set-trust cloud_token (no allow-smb) → get MaaS token
        3d. publish → wait for task
        4.  Update Zero Touch user-script with MaaS token, remove under-construction
        5.  Configure VPN interface (if VPN community specified)
        6.  Get activation link
        7.  wait_for_cloud_token_communicating (test-trust, trust-details.status == communicating)
        8.  set-simple-gateway one-time-password (real SIC OTP)
        9.  publish → wait for task
        10. wait_for_sic_established (test-trust, sic-status == communicating)
        11. get-interfaces → publish → wait for task
        12-15. VPN community, policy install
        
        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            template_id: Zero Touch template ID
            template_name: Template name (to detect Spark vs Gaia)
            gateway_name: Name for the gateway
            user_script: User script template with <token> placeholder
            time_zone: Gateway timezone
            sic_otp: SIC one-time password
            gateway_type: Smart-1 Cloud gateway type
            identification_method: How Smart-1 Cloud identifies gateway
            status_callback: Optional callback for status updates
            
        Returns:
            Complete deployment result with all details
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

        result = {
            "steps": [],
            "success": False,
            "gateway_name": gateway_name,
            "mac_address": mac_address
        }
        
        try:
            # Determine if Spark or Gaia
            is_spark = self.zero_touch._is_spark_gateway(template_name)
            await send_status(0, f"Starting Smart-1 Cloud deployment for {'Spark' if is_spark else 'Gaia'} gateway: {gateway_name}")
            
            # Step 1: Authenticate with both services
            await send_status(1, "Authenticating with Zero Touch and Smart-1 Cloud")
            zt_login = await self.zero_touch.login()
            if not zt_login.success:
                raise Exception(f"Zero Touch login failed: {zt_login.message}")
            await self.smart1_cloud.login()
            result["steps"].append({
                "step": 1,
                "name": "Authentication",
                "status": "success"
            })
            
            # Step 2: Claim gateway in Zero Touch with template (skip if already claimed via UI)
            if template_id:
                await send_status(2, f"Claiming gateway {gateway_name} in Zero Touch (template: {template_name})...")
                try:
                    claim_response = await self.zero_touch.claim_gateway(
                        mac_address=mac_address,
                        gateway_name=gateway_name,
                        template_id=template_id,
                        account_id=account_id,
                        custom_settings={
                            "under_construction": True,
                            "time_zone": time_zone
                        }
                    )
                    logger.info(f"Gateway '{gateway_name}' claimed in Zero Touch")
                    result["steps"].append({
                        "step": 2,
                        "name": "Claim Gateway (Zero Touch)",
                        "status": "success",
                        "data": claim_response.data if hasattr(claim_response, 'data') else {}
                    })
                except Exception as e:
                    # Gateway may already be claimed — try to read existing config and continue
                    logger.warning(f"Claim failed (may already be claimed): {e}")
                    await send_status(2, f"Claim skipped — gateway may already be claimed in Zero Touch")
                    result["steps"].append({
                        "step": 2,
                        "name": "Claim Gateway (Zero Touch)",
                        "status": "warning",
                        "message": str(e)
                    })
            else:
                await send_status(2, f"Skipping claim — gateway already claimed via UI (no template_id)")
                result["steps"].append({
                    "step": 2,
                    "name": "Claim Gateway (Zero Touch)",
                    "status": "skipped",
                    "message": "Gateway already claimed in previous step"
                })
            
            # Step 3: Create gateway in Smart-1 Cloud
            # Both Spark and Gaia start with add-simple-gateway (name + auto-generate-ip only),
            # then set-simple-gateway and set-trust in separate calls.
            await send_status(3, f"Creating gateway in Smart-1 Cloud ({'Spark' if is_spark else 'Gaia'})...")

            # Step 3a: add-simple-gateway (name + auto-generate-ip only — always for both Spark and Gaia)
            # Smart-1 Cloud always uses auto-generate-ip; ipv4_address is for Zero Touch only.
            await send_status(3, "add-simple-gateway (name + auto-generate-ip)...")
            try:
                smart1_response = await self.smart1_cloud.create_gateway(
                    gateway_name=gateway_name,
                    sic_otp="",           # No OTP at creation for either type
                    hardware=None,        # Set via set-simple-gateway
                    auto_generate_ip=True,
                    ip_address=None,
                    version=None,         # Set via set-simple-gateway
                    firewall=firewall,
                    vpn=vpn,
                    ips=ips,
                    application_control=application_control,
                    url_filtering=url_filtering,
                    anti_bot=anti_bot,
                    anti_virus=anti_virus,
                    threat_emulation=threat_emulation,
                    content_awareness=content_awareness,
                )
                logger.info("add-simple-gateway completed")
            except Exception as create_err:
                # Gateway may already exist — check before failing
                logger.warning(f"add-simple-gateway failed: {create_err}. Checking if gateway already exists...")
                try:
                    smart1_response = await self.smart1_cloud.get_gateway(gateway_name)
                    logger.info(f"Gateway '{gateway_name}' already exists in Smart-1 Cloud, continuing with existing gateway")
                    await send_status(3, f"Gateway '{gateway_name}' already exists, continuing...")
                except Exception:
                    # Gateway doesn't exist and creation failed — re-raise original error
                    raise create_err

            # Step 3b: set-simple-gateway (version + hardware; allow-smb only for Spark)
            await send_status(3, "set-simple-gateway (version, hardware)...")
            await self.smart1_cloud.set_simple_gateway(
                gateway_name=gateway_name,
                version=os_version,
                hardware=hardware,
                allow_smb=is_spark,   # True for Spark, False (omitted) for Gaia
            )
            logger.info(f"set-simple-gateway completed ({'Spark' if is_spark else 'Gaia'})")

            if is_spark:
                # Step 3c (Spark): set-trust with OTP + when_gateway_connects
                await send_status(3, "Setting trust with OTP (set-trust #1, Spark)...")
                await self.smart1_cloud.set_trust(
                    gateway_name=gateway_name,
                    trust_method=None,
                    one_time_password=sic_otp,
                    initiation_phase="when_gateway_connects",
                    allow_smb=True,
                )
                logger.info("set-trust (OTP) completed (Spark)")

            # Step 3d: set-trust cloud_token → get MaaS token (both Spark and Gaia)
            await send_status(3, "set-trust cloud_token to obtain MaaS token...")
            set_trust_response = await self.smart1_cloud.set_trust(
                gateway_name=gateway_name,
                trust_method="cloud_token",
                allow_smb=is_spark,   # True for Spark, False (omitted) for Gaia
            )
            logger.info(f"set-trust (cloud_token) completed ({'Spark' if is_spark else 'Gaia'})")

            # Step 3e: publish → wait for task (handled inside publish())
            await send_status(3, "Publishing gateway creation changes...")
            await self.smart1_cloud.publish()
            logger.info("Gateway creation published")

            if is_spark:
                # Step 3f (Spark): show-simple-gateway → get WAN IP + MaaS token
                await send_status(3, "Reading gateway object (show-simple-gateway, Spark)...")
                smart1_response = await self.smart1_cloud.get_gateway(gateway_name)

            result["steps"].append({
                "step": 3,
                "name": "Create Gateway (Smart-1 Cloud)",
                "status": "success",
            })


            # Extract MaaS token (authentication-token):
            # For both Spark and Gaia: prefer set-trust cloud_token response (most reliable source)
            # Fallback: show-simple-gateway response (Spark) or add-simple-gateway response (Gaia)
            maas_token = set_trust_response.get("cloud_token")
            if not maas_token:
                trust_details = set_trust_response.get("trust-details", {})
                maas_token = trust_details.get("authentication-token")
            if not maas_token and isinstance(smart1_response, dict):
                maas_token = smart1_response.get("cloud_token")
                if not maas_token:
                    trust_details = smart1_response.get("trust-details", {})
                    maas_token = trust_details.get("authentication-token")

            if not maas_token:
                logger.error("authentication-token not found in set-trust or gateway response")
                logger.debug(f"set-trust response keys: {list(set_trust_response.keys())}")
                raise ValueError("authentication-token not found in Smart-1 Cloud response. Check gateway creation in SmartConsole.")

            logger.info(f"Extracted cloud_token (MaaS token): {maas_token[:20]}...")
            result["maas_token"] = maas_token[:20] + "..." if len(maas_token) > 20 else maas_token

            # Extract the auto-generated WAN IP
            wan_ip = smart1_response.get("ipv4-address") if isinstance(smart1_response, dict) else None
            if wan_ip:
                logger.info(f"Gateway WAN IP address: {wan_ip}")
                result["wan_ip"] = wan_ip

            # Step 4: Update Zero Touch user-script with SIC key + MaaS token, remove under-construction
            await send_status(4, "Updating Zero Touch user-script with SIC key and MaaS token...")
            try:
                gateway_config = await self.zero_touch.get_gateway_configuration(
                    mac_address=mac_address,
                    account_id=account_id,
                    template_name=template_name
                )
                current_user_script = gateway_config.get('user-script', user_script)
                logger.info(f"Retrieved current user-script ({len(current_user_script)} chars)")
            except Exception as e:
                logger.warning(f"Could not get gateway configuration: {e}. Using provided user-script.")
                current_user_script = user_script

            # Inject ##!! file references and replace <gateway-name>
            current_user_script = apply_script_substitutions(current_user_script, gateway_name)

            # Replace MaaS token (applies to all gateways)
            updated_script = current_user_script.replace("<token>", maas_token)

            # Replace SIC key based on gateway type
            if is_spark:
                updated_script = updated_script.replace("<sic-key>", sic_otp)
                logger.debug("Replaced <sic-key> for Spark gateway")
            else:
                updated_script = updated_script.replace("<ftw-sic-key>", sic_otp)
                logger.debug("Replaced <ftw-sic-key> for Gaia gateway")

            # Validate replacements
            if "<token>" in updated_script:
                logger.warning("Token placeholder still present after replacement!")
            if is_spark and "<sic-key>" in updated_script:
                logger.warning("Spark SIC key placeholder still present after replacement!")
            if not is_spark and "<ftw-sic-key>" in updated_script:
                logger.warning("Gaia FTW SIC key placeholder still present after replacement!")

            logger.debug(f"Updated script ({len(updated_script)} chars):\n{updated_script[:200]}...")

            # Update gateway configuration and remove under-construction flag
            await send_status(4, "Pushing updated clish script and removing under-construction flag...")
            try:
                update_settings: Dict[str, Any] = {
                    "user_script": updated_script,
                    "under_construction": False,
                    "hostname": gateway_name,
                    # time_zone is intentionally omitted — the current config already has
                    # the correct timezone (set during claim). Overriding it with the CSV
                    # value causes error 17013 ("value of timeZone is invalid") from the API.
                }
                # For Gaia gateways, also set ftw-sic-key and management IP in Zero Touch
                if not is_spark:
                    update_settings["ftw-sic-key"] = sic_otp
                    logger.info("Including ftw-sic-key in Zero Touch update (Gaia gateway)")
                    if ipv4_address:
                        update_settings["mgmt-eth-ip-address-ipv4"] = ipv4_address
                        logger.info(f"Including mgmt-eth-ip-address-ipv4={ipv4_address} in Zero Touch update (Gaia gateway)")

                update_response = await self.zero_touch.update_gateway_configuration(
                    mac_address=mac_address,
                    account_id=account_id,
                    template_name=template_name,
                    settings=update_settings
                )
                result["steps"].append({
                    "step": 4,
                    "name": "Update User-Script and Remove Under-Construction",
                    "status": "success",
                    "data": update_response
                })
            except Exception as zt_err:
                logger.warning(f"Step 4 Zero Touch update failed (MAC may not be claimed yet): {zt_err}")
                await send_status(4, f"Warning: Zero Touch update skipped — MAC {mac_address} not found in Zero Touch portal. Claim the gateway first.", status="warning")
                result["steps"].append({
                    "step": 4,
                    "name": "Update User-Script and Remove Under-Construction",
                    "status": "skipped",
                    "warning": f"MAC {mac_address} not found in Zero Touch portal — claim the gateway and retry this step."
                })

            # Steps 6–10: Gaia-only post-publish flow
            activation_link = None
            if not is_spark:
                # Step 6: Get activation link
                await send_status(6, "Getting activation link for Gaia gateway...")
                try:
                    url = f"{self.zero_touch.base_url}/show-gaia-claimed-gateway"
                    headers = self.zero_touch._get_headers()
                    payload = {
                        'account-id': int(account_id),
                        'mac': mac_address.upper()
                    }
                    log_http_request(logger.debug, "show-gaia-claimed-gateway", payload)
                    response = await self.zero_touch.client.post(url, headers=headers, json=payload)
                    log_http_response(logger.debug, "show-gaia-claimed-gateway", response)
                    response.raise_for_status()
                    gaia_details = response.json()
                    activation_url_key = gaia_details.get("activation-url-key")
                    if activation_url_key:
                        activation_link = f"https://zerotouch.checkpoint.com/ZeroTouch/activatelink/{activation_url_key}"
                        logger.info(f"Activation link: {activation_link}")
                    result["steps"].append({
                        "step": 6,
                        "name": "Get Activation Link (Gaia)",
                        "status": "success",
                        "activation_link": activation_link
                    })
                except Exception as e:
                    logger.warning(f"Could not get activation link: {e}")
                    result["steps"].append({
                        "step": 6,
                        "name": "Get Activation Link (Gaia)",
                        "status": "warning",
                        "message": str(e)
                    })

                # Step 7: Wait for cloud-token to reach "communicating" (gateway connected, token active)
                await send_status(7, "Waiting for gateway to connect (cloud-token communicating)...")
                token_communicating = False
                try:
                    token_result = await self.smart1_cloud.wait_for_cloud_token_communicating(
                        gateway_name=gateway_name,
                        timeout=settings.sic_timeout,
                        poll_interval=10,
                        status_callback=lambda info: send_status(7, info.get("message", "Checking cloud-token status..."))
                    )
                    if token_result.get("success"):
                        token_communicating = True
                        result["steps"].append({
                            "step": 7,
                            "name": "Wait for Cloud-Token Communicating (Gaia)",
                            "status": "success",
                            "trust_status": token_result.get("trust_status")
                        })
                    else:
                        result["steps"].append({
                            "step": 7,
                            "name": "Wait for Cloud-Token Communicating (Gaia)",
                            "status": "warning",
                            "message": token_result.get("error")
                        })
                except TimeoutError as e:
                    logger.warning(f"Cloud-token communicating timed out: {e}")
                    result["steps"].append({
                        "step": 7,
                        "name": "Wait for Cloud-Token Communicating (Gaia)",
                        "status": "warning",
                        "message": str(e)
                    })
                except Exception as e:
                    logger.warning(f"Cloud-token communicating check failed: {e}")
                    result["steps"].append({
                        "step": 7,
                        "name": "Wait for Cloud-Token Communicating (Gaia)",
                        "status": "warning",
                        "message": str(e)
                    })

                if token_communicating:
                    # Step 8: set-simple-gateway with real SIC OTP
                    await send_status(8, "Setting SIC one-time-password (set-simple-gateway)...")
                    try:
                        await self.smart1_cloud.set_gateway_sic_password(
                            gateway_name=gateway_name,
                            one_time_password=sic_otp
                        )
                        result["steps"].append({
                            "step": 8,
                            "name": "Set SIC OTP (Gaia)",
                            "status": "success"
                        })

                        # Step 9: Publish SIC OTP change → wait for task (handled inside publish())
                        await send_status(9, "Publishing SIC OTP change...")
                        await self.smart1_cloud.publish()
                        result["steps"].append({
                            "step": 9,
                            "name": "Publish SIC OTP (Gaia)",
                            "status": "success"
                        })

                    except Exception as e:
                        logger.warning(f"Set SIC OTP or publish failed: {e}")
                        result["steps"].append({
                            "step": 8,
                            "name": "Set SIC OTP (Gaia)",
                            "status": "warning",
                            "message": str(e)
                        })
            
            # Step 11: Poll test-trust until sic-status == "communicating"
            # (only meaningful for Gaia — Spark gateways use OTP trust, SIC is set differently)
            await send_status(11, "Waiting for SIC to become communicating (polling every 10 seconds)...")
            sic_established = False
            try:
                sic_result = await self.smart1_cloud.wait_for_sic_communicating(
                    gateway_name=gateway_name,
                    timeout=settings.sic_timeout,
                    poll_interval=10,
                    status_callback=lambda info: send_status(11, info.get("message", "Checking SIC status..."))
                )

                if sic_result.get("success"):
                    sic_established = True
                    result["steps"].append({
                        "step": 11,
                        "name": "Wait for SIC Communicating",
                        "status": "success",
                        "sic_status": sic_result.get("sic_status")
                    })
                else:
                    logger.warning(f"SIC communicating check issue: {sic_result.get('error')}")
                    result["steps"].append({
                        "step": 11,
                        "name": "Wait for SIC Communicating",
                        "status": "warning",
                        "message": sic_result.get("error")
                    })
            except TimeoutError as e:
                logger.warning(f"SIC communicating timed out: {e}")
                result["steps"].append({
                    "step": 11,
                    "name": "Wait for SIC Communicating",
                    "status": "warning",
                    "message": "SIC communicating timed out. Gateway may still be connecting."
                })
            except Exception as e:
                logger.warning(f"Could not monitor SIC status: {e}")
                result["steps"].append({
                    "step": 11,
                    "name": "Wait for SIC Communicating",
                    "status": "warning",
                    "message": str(e)
                })

            if not sic_established:
                logger.warning("SIC not established — skipping interfaces, topology, VPN and policy steps")
                await send_status(11, "SIC not yet established. Skipping remaining steps.", "warning")
                result["success"] = True
                result["activation_link"] = activation_link
                result["gateway_type"] = "Spark" if is_spark else "Gaia"
                result["message"] = f"Gateway {gateway_name} created. SIC not yet established — remaining steps skipped."
                return result
            
            # Step 12: Get interfaces with topology (async task, retries on 500)
            await send_status(12, "Getting gateway interfaces with topology (retrying if needed)...")
            try:
                interfaces_result = await self.smart1_cloud.get_interfaces(
                    gateway_name=gateway_name,
                    with_topology=True,
                    use_defined_by_routes=False if is_spark else True,
                    group_interfaces_by_subnet=True,
                    allow_smb=is_spark,
                    ignore_sic_status=is_spark
                )
                result["steps"].append({
                    "step": 12,
                    "name": "Get Interfaces with Topology",
                    "status": "success",
                    "interfaces_count": len(interfaces_result.get("interfaces", []))
                })
            except Exception as e:
                logger.warning(f"Could not get interfaces: {e}")
                result["steps"].append({
                    "step": 12,
                    "name": "Get Interfaces with Topology",
                    "status": "warning",
                    "message": str(e)
                })
            
            # Step 13: Publish topology changes
            await send_status(13, "Publishing topology changes...")
            try:
                await self.smart1_cloud.publish()
                result["steps"].append({
                    "step": 13,
                    "name": "Publish Topology Changes",
                    "status": "success"
                })
            except Exception as e:
                logger.warning(f"Publish topology failed: {e}")
                result["steps"].append({
                    "step": 13,
                    "name": "Publish Topology Changes",
                    "status": "warning",
                    "message": str(e)
                })

            # Step 13b: Set security blades via set-simple-gateway, then publish + wait
            await send_status(13, "Setting security blades on gateway...")
            try:
                blades_payload = {
                    "firewall": firewall,
                    "vpn": vpn,
                    "ips": ips,
                    "application-control": application_control,
                    "url-filtering": url_filtering,
                    "anti-bot": anti_bot,
                    "anti-virus": anti_virus,
                    "threat-emulation": threat_emulation,
                }
                await self.smart1_cloud.set_simple_gateway(
                    gateway_name=gateway_name,
                    allow_smb=is_spark,
                    **blades_payload
                )
                logger.info(f"Security blades set on gateway '{gateway_name}'")

                await send_status(13, "Publishing security blade changes...")
                await self.smart1_cloud.publish()
                result["steps"].append({
                    "step": "13b",
                    "name": "Set Security Blades + Publish",
                    "status": "success",
                    "blades": blades_payload
                })
            except Exception as e:
                logger.warning(f"Set security blades failed: {e}")
                result["steps"].append({
                    "step": "13b",
                    "name": "Set Security Blades + Publish",
                    "status": "warning",
                    "message": str(e)
                })

            # Step 13c: Configure VPN interface (after blades are set)
            if vpn_community:
                await send_status(14, "Configuring VPN interface for gateway")
                try:
                    await self.smart1_cloud.configure_vpn_interface(
                        gateway_name=gateway_name,
                        is_spark=is_spark
                    )
                    logger.info("Configured VPN interface for gateway")
                    result["steps"].append({
                        "step": "13c",
                        "name": "Configure VPN Interface",
                        "status": "success"
                    })
                except Exception as e:
                    logger.warning(f"Configure VPN interface failed: {e}")
                    result["steps"].append({
                        "step": "13c",
                        "name": "Configure VPN Interface",
                        "status": "warning",
                        "message": str(e)
                    })

            # Step 14: Add gateway to VPN community (if specified)
            if vpn_community:
                await send_status(14, f"Adding gateway to VPN community '{vpn_community}' as {vpn_role}")
                try:
                    await self.smart1_cloud.add_gateway_to_vpn_community(
                        gateway_name=gateway_name,
                        community_name=vpn_community,
                        role=vpn_role
                    )
                    logger.info(f"Added gateway to VPN community '{vpn_community}' as {vpn_role}")
                    result["steps"].append({
                        "step": 14,
                        "name": f"Add to VPN Community ({vpn_role})",
                        "status": "success",
                        "vpn_community": vpn_community,
                        "vpn_role": vpn_role
                    })
                except Exception as e:
                    logger.warning(f"Failed to add gateway to VPN community: {e}")
                    result["steps"].append({
                        "step": 14,
                        "name": "Add to VPN Community",
                        "status": "warning",
                        "message": str(e)
                    })
            
            # Step 15: Publish VPN community changes
            if vpn_community:
                await send_status(15, "Publishing VPN community changes...")
                try:
                    await self.smart1_cloud.publish()
                    result["steps"].append({
                        "step": 15,
                        "name": "Publish VPN Changes",
                        "status": "success"
                    })
                except Exception as e:
                    logger.warning(f"Publish VPN changes failed: {e}")
                    result["steps"].append({
                        "step": 15,
                        "name": "Publish VPN Changes",
                        "status": "warning",
                        "message": str(e)
                    })
            
            # Step 16: Install policy
            if policy_name:
                await send_status(16, f"Installing policy '{policy_name}'...")
                try:
                    install_result = await self.smart1_cloud.install_policy(
                        gateway_name=gateway_name,
                        policy_package=policy_name,
                        access=True,
                        threat_prevention=threat_emulation or anti_bot or anti_virus
                    )
                    task_id = install_result.get("task-id")
                    if task_id:
                        await send_status(16, "Waiting for policy install to finish (checking every 5s)...")
                        await self.smart1_cloud.wait_for_task(task_id, timeout=300, poll_interval=5)
                    logger.info(f"Policy '{policy_name}' installed successfully")
                    result["steps"].append({
                        "step": 16,
                        "name": "Install Policy",
                        "status": "success",
                        "policy_name": policy_name
                    })
                except Exception as e:
                    logger.warning(f"Policy install failed: {e}")
                    result["steps"].append({
                        "step": 16,
                        "name": "Install Policy",
                        "status": "warning",
                        "message": str(e)
                    })
            
            # Success!
            final_step = 16 if policy_name else (15 if vpn_community else 13)
            await send_status(final_step, "Smart-1 Cloud deployment completed successfully", "completed")
            result["success"] = True
            result["activation_link"] = activation_link
            result["gateway_type"] = "Spark" if is_spark else "Gaia"
            result["message"] = (
                f"Gateway {gateway_name} successfully deployed! "
                f"{'Activation link generated.' if activation_link else 'Ready for download.'}"
            )
            
            logger.info(f"✓ Complete deployment successful for {gateway_name}")
            return result
            
        except Exception as e:
            logger.error(f"Deployment failed: {str(e)}")
            result["success"] = False
            result["error"] = str(e)
            result["steps"].append({
                "step": "error",
                "name": "Deployment Failed",
                "status": "error",
                "message": str(e)
            })
            raise
