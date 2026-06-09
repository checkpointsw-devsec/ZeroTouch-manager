"""
API endpoint for orchestrated gateway deployment.
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from typing import Dict, Any, Optional, AsyncGenerator
from pydantic import BaseModel, Field
from loguru import logger

from ..services.deployment_orchestrator import GatewayDeploymentOrchestrator
from ..services.lsm_deployment_orchestrator import LSMDeploymentOrchestrator
from ..services.sms_deployment_orchestrator import SMSDeploymentOrchestrator
from ..services.smp_deployment_orchestrator import SMPDeploymentOrchestrator
from ..services.sms_service import SMSService
from ..services.zero_touch_service import ZeroTouchService
from ..services.sdwan_service import SDWANService


router = APIRouter(prefix="/api/deployment", tags=["Gateway Deployment"])


def make_json_serializable(obj: Any) -> Any:
    """Recursively convert Pydantic models to dicts for JSON serialization."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    return obj


class DeployGatewayRequest(BaseModel):
    """Request model for complete gateway deployment."""
    # Zero Touch parameters
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    template_id: Optional[str] = Field(None, description="Zero Touch template ID (required to claim unclaimed gateways)")
    template_name: str = Field(..., description="Template name")
    gateway_name: str = Field(..., description="Gateway name/hostname")
    user_script: str = Field(..., description="User script with <token> placeholder")
    time_zone: str = Field(..., description="Gateway timezone")
    
    # Smart-1 Cloud parameters
    sic_otp: str = Field(..., min_length=4, description="SIC one-time password (min 4 chars)")
    hardware: Optional[str] = Field(None, description="Hardware type (e.g., 'Open server', 'Check Point 6200'). Leave empty for auto-detect.")
    gateway_type: str = Field(
        default="APPLIANCE_OR_OPENSERVER",
        description="Smart-1 Cloud gateway type"
    )
    identification_method: str = Field(
        default="GATEWAY_NAME",
        description="Gateway identification method"
    )
    
    # OS version (for Spark gateways)
    os_version: Optional[str] = Field(default="R81.10", description="OS version: R81.10 (default for Spark) or R82")
    
    # Security blades
    firewall: bool = Field(default=True, description="Enable Firewall blade")
    vpn: bool = Field(default=True, description="Enable VPN blade")
    ips: bool = Field(default=True, description="Enable IPS blade")
    application_control: bool = Field(default=True, description="Enable Application Control blade")
    url_filtering: bool = Field(default=True, description="Enable URL Filtering blade")
    anti_bot: bool = Field(default=True, description="Enable Anti-Bot blade")
    anti_virus: bool = Field(default=True, description="Enable Anti-Virus blade")
    threat_emulation: bool = Field(default=True, description="Enable Threat Emulation blade")
    content_awareness: bool = Field(default=False, description="Enable Content Awareness blade")
    
    # VPN community (optional)
    vpn_community: Optional[str] = Field(None, description="VPN community to add the gateway to")
    vpn_role: str = Field(default="satellite", description="VPN gateway role: 'center' or 'satellite'")

    # Policy installation
    policy_name: Optional[str] = Field(None, description="Policy package to install after gateway deployment")

    # IPv4 address (required for non-Spark/Gaia gateways; omit or leave empty for Spark auto-generate-ip)
    ipv4_address: Optional[str] = Field(None, description="Gateway IPv4 address (Gaia only). If provided, used instead of auto-generate-ip for add-simple-gateway and set as mgmt-eth-ip-address-ipv4 in Zero Touch.")


class DeployLSMGatewayRequest(BaseModel):
    """Request model for LSM gateway deployment (Spark gateways only)."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    template_id: Optional[str] = Field(None, description="Zero Touch template ID (required to claim unclaimed gateways)")
    template_name: str = Field(..., description="Template name")
    gateway_name: str = Field(..., description="Gateway name/hostname")
    mgmt_server_ip: Optional[str] = Field(None, description="Management Server IP address (resolved from user-script if not provided)")
    sic_otp: Optional[str] = Field(None, description="SIC one-time password (resolved from user-script if not provided)")
    security_profile: Optional[str] = Field(None, description="LSM security profile name")
    provisioning_profile: Optional[str] = Field(None, description="LSM provisioning profile name")
    domain: Optional[str] = Field(None, description="Domain for Multi-Domain Server")
    gateway_ipv4: Optional[str] = Field(None, description="Gateway IPv4 address to set as mgmt-eth-ip-address-ipv4 in Zero Touch (overrides template default)")


class DeploySMSGatewayRequest(BaseModel):
    """Request model for SMS/SMP gateway deployment."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    template_id: Optional[str] = Field(None, description="Zero Touch template ID (required to claim unclaimed gateways)")
    template_name: str = Field(..., description="Template name")
    gateway_name: str = Field(..., description="Gateway name/hostname")
    mgmt_server_ip: str = Field(..., description="Management Server IP address")
    sic_otp: str = Field(..., min_length=4, description="SIC one-time password (min 4 chars)")
    gateway_ipv4: str = Field(..., description="Gateway IPv4 address for the management server object")
    version: str = Field(..., description="Gateway software version (e.g. R81.10)")
    hardware: str = Field(..., description="Gateway hardware model (e.g. Check Point 1590)")
    policy_name: str = Field(default="Standard", description="Security policy name")
    enable_app_control: bool = Field(default=True, description="Enable Application Control")
    enable_ips: bool = Field(default=True, description="Enable IPS")
    enable_url_filtering: bool = Field(default=False, description="Enable URL Filtering")
    enable_content_awareness: bool = Field(default=False, description="Enable Content Awareness")
    enable_ipsec: bool = Field(default=True, description="Enable IPsec VPN")
    enable_anti_bot: bool = Field(default=True, description="Enable Anti-Bot")
    enable_anti_virus: bool = Field(default=True, description="Enable Anti-Virus")
    enable_threat_emulation: bool = Field(default=True, description="Enable Threat Emulation")
    vpn_community: Optional[str] = Field(None, description="VPN community name")
    vpn_role: str = Field(default="satellite", description="VPN role: center or satellite")
    domain: Optional[str] = Field(None, description="Domain for Multi-Domain Server")


@router.post("/deploy-with-smart1-cloud")
async def deploy_gateway_with_smart1_cloud(
    request: DeployGatewayRequest
) -> Dict[str, Any]:
    """
    Complete gateway deployment orchestration.

    This endpoint performs the complete workflow:
    1. Claim gateway in Zero Touch Portal
    2. Create gateway in Smart-1 Cloud
    3. Extract MaaS token from Smart-1 Cloud response
    4. Update Zero Touch user-script with MaaS token
    5. Remove under-construction flag
    6. For Gaia gateways: Provide activation link

    Returns complete deployment status with all steps.
    """
    try:
        async with GatewayDeploymentOrchestrator() as orchestrator:
            result = await orchestrator.deploy_gateway_with_smart1_cloud(
                mac_address=request.mac_address,
                account_id=request.account_id,
                template_id=request.template_id,
                template_name=request.template_name,
                gateway_name=request.gateway_name,
                user_script=request.user_script,
                time_zone=request.time_zone,
                sic_otp=request.sic_otp,
                hardware=request.hardware,
                gateway_type=request.gateway_type,
                identification_method=request.identification_method,
                os_version=request.os_version,
                firewall=request.firewall,
                vpn=request.vpn,
                ips=request.ips,
                application_control=request.application_control,
                url_filtering=request.url_filtering,
                anti_bot=request.anti_bot,
                anti_virus=request.anti_virus,
                threat_emulation=request.threat_emulation,
                content_awareness=request.content_awareness,
                vpn_community=request.vpn_community,
                vpn_role=request.vpn_role,
                policy_name=request.policy_name,
                ipv4_address=request.ipv4_address
            )

            return result

    except Exception as e:
        logger.error(f"Gateway deployment failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Gateway deployment failed: {str(e)}"
        )


@router.post("/deploy-with-smart1-cloud/stream")
async def deploy_gateway_with_smart1_cloud_stream(
    request: DeployGatewayRequest
) -> StreamingResponse:
    """
    Deploy gateway to Smart-1 Cloud with SSE for real-time status updates.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        status_queue = asyncio.Queue()

        def status_callback(status_info: Dict[str, Any]):
            asyncio.create_task(status_queue.put(status_info))

        async def run_deployment():
            try:
                async with GatewayDeploymentOrchestrator() as orchestrator:
                    result = await orchestrator.deploy_gateway_with_smart1_cloud(
                        mac_address=request.mac_address,
                        account_id=request.account_id,
                        template_id=request.template_id,
                        template_name=request.template_name,
                        gateway_name=request.gateway_name,
                        user_script=request.user_script,
                        time_zone=request.time_zone,
                        sic_otp=request.sic_otp,
                        hardware=request.hardware,
                        gateway_type=request.gateway_type,
                        identification_method=request.identification_method,
                        os_version=request.os_version,
                        firewall=request.firewall,
                        vpn=request.vpn,
                        ips=request.ips,
                        application_control=request.application_control,
                        url_filtering=request.url_filtering,
                        anti_bot=request.anti_bot,
                        anti_virus=request.anti_virus,
                        threat_emulation=request.threat_emulation,
                        content_awareness=request.content_awareness,
                        vpn_community=request.vpn_community,
                        vpn_role=request.vpn_role,
                        policy_name=request.policy_name,
                        ipv4_address=request.ipv4_address,
                        status_callback=status_callback
                    )
                    await status_queue.put({"type": "complete", "result": result})
            except Exception as e:
                logger.error(f"Smart-1 Cloud deployment failed: {str(e)}")
                await status_queue.put({"type": "error", "error": str(e)})

        deployment_task = asyncio.create_task(run_deployment())

        try:
            while True:
                try:
                    status = await asyncio.wait_for(status_queue.get(), timeout=120.0)
                    
                    if status.get("type") == "complete":
                        # Convert any Pydantic models to dicts for JSON serialization
                        serializable_result = make_json_serializable(status['result'])
                        yield f"data: {json.dumps({'event': 'complete', 'data': serializable_result})}\n\n"
                        break
                    elif status.get("type") == "error":
                        yield f"data: {json.dumps({'event': 'error', 'data': {'error': status['error']}})}\n\n"
                        break
                    else:
                        serializable_status = make_json_serializable(status)
                        yield f"data: {json.dumps({'event': 'status', 'data': serializable_status})}\n\n"
                        
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
                    
        except asyncio.CancelledError:
            deployment_task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


@router.post("/deploy-with-lsm")
async def deploy_gateway_with_lsm(
    request: DeployLSMGatewayRequest
) -> Dict[str, Any]:
    """
    Deploy gateway to LSM (Large Scale Management).

    This endpoint performs the complete workflow:
    1. Login to Zero Touch Portal
    2. Get gateway configuration
    3. Replace SIC key in user-script (Spark only)
    4. Update gateway configuration with SIC key
    5. Login to Management Server
    6. Add LSM gateway
    7. Publish changes
    8. Remove under-construction flag
    9. For Gaia gateways: Provide activation link

    Returns complete deployment status with activation link if applicable.
    """
    try:
        async with LSMDeploymentOrchestrator() as orchestrator:
            result = await orchestrator.deploy_gateway(
                mac_address=request.mac_address,
                account_id=request.account_id,
                template_id=request.template_id,
                template_name=request.template_name,
                gateway_name=request.gateway_name,
                mgmt_server_ip=request.mgmt_server_ip,
                sic_otp=request.sic_otp,
                security_profile=request.security_profile,
                provisioning_profile=request.provisioning_profile,
                domain=request.domain,
                gateway_ipv4=request.gateway_ipv4
            )

            return result

    except Exception as e:
        logger.error(f"LSM deployment failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"LSM deployment failed: {str(e)}"
        )


@router.post("/deploy-with-lsm/stream")
async def deploy_gateway_with_lsm_stream(
    request: DeployLSMGatewayRequest
) -> StreamingResponse:
    """
    Deploy gateway to LSM with SSE for real-time status updates.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        status_queue = asyncio.Queue()

        def status_callback(status_info: Dict[str, Any]):
            asyncio.create_task(status_queue.put(status_info))

        async def run_deployment():
            try:
                async with LSMDeploymentOrchestrator() as orchestrator:
                    result = await orchestrator.deploy_gateway(
                        mac_address=request.mac_address,
                        account_id=request.account_id,
                        template_id=request.template_id,
                        template_name=request.template_name,
                        gateway_name=request.gateway_name,
                        mgmt_server_ip=request.mgmt_server_ip,
                        sic_otp=request.sic_otp,
                        security_profile=request.security_profile,
                        provisioning_profile=request.provisioning_profile,
                        domain=request.domain,
                        gateway_ipv4=request.gateway_ipv4,
                        status_callback=status_callback
                    )
                    await status_queue.put({"type": "complete", "result": result})
            except Exception as e:
                logger.error(f"LSM deployment failed: {str(e)}")
                await status_queue.put({"type": "error", "error": str(e)})

        deployment_task = asyncio.create_task(run_deployment())

        try:
            while True:
                try:
                    status = await asyncio.wait_for(status_queue.get(), timeout=120.0)
                    
                    if status.get("type") == "complete":
                        # Convert any Pydantic models to dicts for JSON serialization
                        serializable_result = make_json_serializable(status['result'])
                        yield f"data: {json.dumps({'event': 'complete', 'data': serializable_result})}\n\n"
                        break
                    elif status.get("type") == "error":
                        yield f"data: {json.dumps({'event': 'error', 'data': {'error': status['error']}})}\n\n"
                        break
                    else:
                        serializable_status = make_json_serializable(status)
                        yield f"data: {json.dumps({'event': 'status', 'data': serializable_status})}\n\n"
                        
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
                    
        except asyncio.CancelledError:
            deployment_task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


@router.post("/deploy-with-sms")
async def deploy_gateway_with_sms(
    request: DeploySMSGatewayRequest
) -> Dict[str, Any]:
    """
    Deploy gateway to SMS (Security Management Server).

    This endpoint performs the complete workflow:
    1. Login to Zero Touch Portal
    2. Get gateway configuration
    3. Replace SIC key and mgmt IP in user-script
    4. Update gateway configuration and start deployment
    5. Extract IPv4/IPv6 addresses
    6. Wait for deployment to finish (poll status)
    7. Login to Management Server
    8. Add simple gateway with security blades
    9. Add to VPN community if specified
    10. Publish changes
    11. For Gaia gateways: Provide activation link

    Returns complete deployment status with activation link if applicable.
    """
    try:
        mgmt_base_url = f"https://{request.mgmt_server_ip}/web_api"
        async with SMSDeploymentOrchestrator(mgmt_base_url=mgmt_base_url) as orchestrator:
            result = await orchestrator.deploy_gateway(
                mac_address=request.mac_address,
                account_id=request.account_id,
                template_id=request.template_id,
                template_name=request.template_name,
                gateway_name=request.gateway_name,
                mgmt_server_ip=request.mgmt_server_ip,
                sic_otp=request.sic_otp,
                gateway_ipv4=request.gateway_ipv4,
                version=request.version,
                hardware=request.hardware,
                policy_name=request.policy_name,
                enable_app_control=request.enable_app_control,
                enable_ips=request.enable_ips,
                enable_url_filtering=request.enable_url_filtering,
                enable_content_awareness=request.enable_content_awareness,
                enable_ipsec=request.enable_ipsec,
                enable_anti_bot=request.enable_anti_bot,
                enable_anti_virus=request.enable_anti_virus,
                enable_threat_emulation=request.enable_threat_emulation,
                vpn_community=request.vpn_community,
                vpn_role=request.vpn_role,
                domain=request.domain
            )

            return result

    except Exception as e:
        logger.error(f"SMS deployment failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"SMS deployment failed: {str(e)}"
        )


@router.post("/deploy-with-sms/stream")
async def deploy_gateway_with_sms_stream(
    request: DeploySMSGatewayRequest
) -> StreamingResponse:
    """
    Deploy gateway to SMS with Server-Sent Events (SSE) for real-time status updates.

    Returns a stream of JSON events with deployment progress.
    Each event has format: {"step": N, "message": "...", "status": "...", "details": {...}}

    Event types:
    - status: "in_progress" - Step is being executed
    - status: "completed" - Step completed successfully
    - status: "error" - Step failed
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        status_queue = asyncio.Queue()

        def status_callback(status_info: Dict[str, Any]):
            """Put status updates into the queue"""
            asyncio.create_task(status_queue.put(status_info))

        async def run_deployment():
            """Run deployment and signal completion"""
            try:
                mgmt_base_url = f"https://{request.mgmt_server_ip}/web_api"
                async with SMSDeploymentOrchestrator(mgmt_base_url=mgmt_base_url) as orchestrator:
                    result = await orchestrator.deploy_gateway(
                        mac_address=request.mac_address,
                        account_id=request.account_id,
                        template_id=request.template_id,
                        template_name=request.template_name,
                        gateway_name=request.gateway_name,
                        mgmt_server_ip=request.mgmt_server_ip,
                        sic_otp=request.sic_otp,
                        gateway_ipv4=request.gateway_ipv4,
                        version=request.version,
                        hardware=request.hardware,
                        policy_name=request.policy_name,
                        enable_app_control=request.enable_app_control,
                        enable_ips=request.enable_ips,
                        enable_url_filtering=request.enable_url_filtering,
                        enable_content_awareness=request.enable_content_awareness,
                        enable_ipsec=request.enable_ipsec,
                        enable_anti_bot=request.enable_anti_bot,
                        enable_anti_virus=request.enable_anti_virus,
                        enable_threat_emulation=request.enable_threat_emulation,
                        vpn_community=request.vpn_community,
                        vpn_role=request.vpn_role,
                        domain=request.domain,
                        status_callback=status_callback
                    )
                    await status_queue.put({"type": "complete", "result": result})
            except Exception as e:
                logger.error(f"SMS deployment failed: {str(e)}")
                await status_queue.put({"type": "error", "error": str(e)})

        # Start deployment in background
        deployment_task = asyncio.create_task(run_deployment())

        try:
            while True:
                try:
                    # Wait for status update with timeout
                    status = await asyncio.wait_for(status_queue.get(), timeout=120.0)
                    
                    # Check for completion or error
                    if status.get("type") == "complete":
                        # Convert any Pydantic models to dicts for JSON serialization
                        serializable_result = make_json_serializable(status['result'])
                        yield f"data: {json.dumps({'event': 'complete', 'data': serializable_result})}\n\n"
                        break
                    elif status.get("type") == "error":
                        yield f"data: {json.dumps({'event': 'error', 'data': {'error': status['error']}})}\n\n"
                        break
                    else:
                        # Regular status update
                        serializable_status = make_json_serializable(status)
                        yield f"data: {json.dumps({'event': 'status', 'data': serializable_status})}\n\n"
                        
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
                    
        except asyncio.CancelledError:
            deployment_task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


class DeploySMPGatewayRequest(BaseModel):
    """Request model for SMP gateway deployment (Zero Touch only, Spark gateways)."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    template_id: Optional[str] = Field(None, description="Zero Touch template ID (required to claim unclaimed gateways)")
    template_name: str = Field(..., description="Template name")
    gateway_name: str = Field(..., description="Gateway name/hostname")


@router.post("/deploy-with-smp/stream")
async def deploy_gateway_with_smp_stream(
    request: DeploySMPGatewayRequest
) -> StreamingResponse:
    """
    Deploy Spark gateway via SMP (Zero Touch only) with SSE for real-time status updates.

    Flow: claim → get config → replace placeholders → update config → remove under-construction
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        status_queue = asyncio.Queue()

        def status_callback(status_info: Dict[str, Any]):
            asyncio.create_task(status_queue.put(status_info))

        async def run_deployment():
            try:
                async with SMPDeploymentOrchestrator() as orchestrator:
                    result = await orchestrator.deploy_gateway(
                        mac_address=request.mac_address,
                        account_id=request.account_id,
                        template_id=request.template_id,
                        template_name=request.template_name,
                        gateway_name=request.gateway_name,
                        status_callback=status_callback
                    )
                    await status_queue.put({"type": "complete", "result": result})
            except Exception as e:
                logger.error(f"SMP deployment failed: {str(e)}")
                await status_queue.put({"type": "error", "error": str(e)})

        deployment_task = asyncio.create_task(run_deployment())

        try:
            while True:
                try:
                    status = await asyncio.wait_for(status_queue.get(), timeout=120.0)
                    if status.get("type") == "complete":
                        serializable_result = make_json_serializable(status['result'])
                        yield f"data: {json.dumps({'event': 'complete', 'data': serializable_result})}\n\n"
                        break
                    elif status.get("type") == "error":
                        yield f"data: {json.dumps({'event': 'error', 'data': {'error': status['error']}})}\n\n"
                        break
                    else:
                        serializable_status = make_json_serializable(status)
                        yield f"data: {json.dumps({'event': 'status', 'data': serializable_status})}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            deployment_task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


class SMSCapabilitiesRequest(BaseModel):
    """Request model for SMS gateway capabilities lookup."""
    mgmt_server_ip: str = Field(..., description="Management Server IP address")
    hardware: Optional[str] = Field(None, description="Filter by hardware model")
    platform: Optional[str] = Field(None, description="Filter by platform (smb, quantum, etc.)")
    version: Optional[str] = Field(None, description="Filter by version")


@router.post("/sms-gateway-capabilities")
async def get_sms_gateway_capabilities(
    request: SMSCapabilitiesRequest
) -> Dict[str, Any]:
    """
    Fetch supported hardware, versions and platforms from the SMS Management Server.

    Calls show-gateway-capabilities on the configured management server and returns
    supported-hardware and supported-versions lists for use in the SMS modal dropdowns.
    """
    mgmt_base_url = f"https://{request.mgmt_server_ip}/web_api"
    async with SMSService(base_url=mgmt_base_url) as sms:
        try:
            await sms.login()
            capabilities = await sms.show_gateway_capabilities(
                hardware=request.hardware,
                platform=request.platform,
                version=request.version
            )

            hardware_list = capabilities.get("supported-hardware", {}).get("hardware", [])
            version_list = capabilities.get("supported-versions", {}).get("versions", [])

            return {
                "success": True,
                "hardware": hardware_list,
                "versions": version_list
            }
        except Exception as e:
            logger.error(f"Failed to fetch SMS gateway capabilities: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch SMS gateway capabilities: {str(e)}")


class ZTGatewayIPRequest(BaseModel):
    """Request model for fetching/updating the gateway IP from Zero Touch."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    template_name: str = Field(..., description="Template name (used to detect Spark vs Gaia)")


class ZTGatewayIPUpdateRequest(BaseModel):
    """Request model for writing back the gateway IP to Zero Touch."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    template_name: str = Field(..., description="Template name (used to detect Spark vs Gaia)")
    gateway_ipv4: str = Field(..., description="New gateway IPv4 address to set")


@router.post("/zt-gateway-ip")
async def get_zt_gateway_ip(request: ZTGatewayIPRequest) -> Dict[str, Any]:
    """
    Fetch the gateway IP address from Zero Touch claimed gateway configuration.

    Returns the ip-address field (for Spark) or mgmt-eth-ip-address-ipv4 (for Gaia).
    Used to pre-populate the Gateway IPv4 field in the SMS modal.
    """
    try:
        async with ZeroTouchService() as zt_service:
            login_result = await zt_service.login()
            if not login_result.success:
                raise HTTPException(status_code=401, detail="Zero Touch login failed")

            config = await zt_service.get_gateway_configuration(
                mac_address=request.mac_address,
                account_id=request.account_id,
                template_name=request.template_name
            )

            is_spark = request.template_name and 'spark' in request.template_name.lower()

            # For Gaia: prefer the writable mgmt IP field; fallback to ip-address
            # For Spark: ip-address is the device-reported IP (read-only in ZT but useful for display)
            if is_spark:
                ip = config.get('ip-address') or config.get('ext-interface-ip') or ''
            else:
                ip = (config.get('mgmt-eth-ip-address-ipv4')
                      or config.get('ip-address')
                      or config.get('ext-interface-ip')
                      or '')

            return {"success": True, "gateway_ipv4": ip, "is_spark": is_spark}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch ZT gateway IP: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch ZT gateway IP: {str(e)}")


@router.post("/zt-gateway-ip/update")
async def update_zt_gateway_ip(request: ZTGatewayIPUpdateRequest) -> Dict[str, Any]:
    """
    Write the gateway IPv4 address back to Zero Touch claimed gateway configuration.

    For Gaia gateways: sets mgmt-eth-ip-address-ipv4.
    For Spark gateways: ip-address is device-reported and read-only in ZT; this
    operation is skipped with a note (the IP is set via the user-script instead).
    """
    is_spark = request.template_name and 'spark' in request.template_name.lower()

    if is_spark:
        logger.info(
            f"Skipping ZT IP update for Spark gateway {request.mac_address}: "
            "ip-address is device-reported (read-only in ZT)"
        )
        return {
            "success": True,
            "skipped": True,
            "reason": "Spark gateway IP is device-reported; update skipped"
        }

    try:
        async with ZeroTouchService() as zt_service:
            login_result = await zt_service.login()
            if not login_result.success:
                raise HTTPException(status_code=401, detail="Zero Touch login failed")

            result = await zt_service.update_gateway_configuration(
                mac_address=request.mac_address,
                account_id=request.account_id,
                template_name=request.template_name,
                settings={'mgmt-eth-ip-address-ipv4': request.gateway_ipv4}
            )

            return {"success": result.success, "message": result.message}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update ZT gateway IP: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update ZT gateway IP: {str(e)}")


class DeploymentStatusRequest(BaseModel):
    """Request model for deployment status check."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Zero Touch account ID")
    is_spark: bool = Field(False, description="True for Spark gateways, False for Gaia")


@router.post("/deployment-status")
async def get_deployment_status(
    request: DeploymentStatusRequest
) -> Dict[str, Any]:
    """
    Get gateway deployment status from Zero Touch Portal.
    
    Returns:
        - mac: Gateway MAC address
        - reported-status-time: Timestamp of last status report
        - reported-display-status: Status - "Not reported", "Installing", "Finished", 
          "Rebooting", "Failed", "Error", "Fetched"
    """
    try:
        async with ZeroTouchService() as zt_service:
            await zt_service.login()

            status = await zt_service.get_gateway_deployment_status(
                mac_address=request.mac_address,
                account_id=request.account_id,
                is_spark=request.is_spark
            )

            return {
                "success": True,
                "status": status
            }

    except Exception as e:
        logger.error(f"Failed to get deployment status: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get deployment status: {str(e)}"
        )


class JoinSDWANProfileRequest(BaseModel):
    """Request model for assigning a gateway to an SD-WAN profile."""
    gateway_name: str = Field(..., description="Gateway name as shown in the Infinity Portal")
    profile_name: str = Field(..., description="SD-WAN profile name to assign the gateway to")


@router.post("/join-sdwan-profile")
async def join_sdwan_profile(
    request: JoinSDWANProfileRequest
) -> Dict[str, Any]:
    """
    Assign a deployed gateway to an SD-WAN profile in the Infinity Portal.

    Workflow:
    1. Authenticate to the Infinity Portal (OAuth)
    2. Resolve the gateway name to its SD-WAN asset id
    3. Resolve the SD-WAN profile name to its id
    4. Add the gateway to the profile
    5. Publish changes
    6. Enforce policy

    Returns a result dict with a human-readable ``steps`` list.
    """
    try:
        async with SDWANService() as sdwan:
            result = await sdwan.join_profile(
                gateway_name=request.gateway_name,
                profile_name=request.profile_name
            )
            return result

    except Exception as e:
        logger.error(f"SD-WAN profile assignment failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"SD-WAN profile assignment failed: {str(e)}"
        )