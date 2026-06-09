"""
API endpoints for Smart-1 Cloud MaaS integration.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any
from loguru import logger

from ..models.smart1_cloud import (
    CreateGatewayRequest,
    EstablishSICRequest,
    ManagementService,
    Gateway,
    GatewayTopology
)
from ..services.smart1_cloud_service import Smart1CloudService


router = APIRouter(prefix="/api/smart1-cloud", tags=["Smart-1 Cloud"])


def _err(e: Exception) -> str:
    """Return a non-empty error description for any exception."""
    return str(e) or type(e).__name__


async def get_smart1_cloud_service():
    """Dependency to get Smart-1 Cloud service instance (async generator for cleanup)."""
    async with Smart1CloudService() as service:
        await service.login()
        yield service


@router.post("/authenticate")
async def authenticate() -> Dict[str, Any]:
    """
    Authenticate with Smart-1 Cloud and obtain session.
    
    Returns session info and authentication status.
    """
    try:
        async with Smart1CloudService() as service:
            session_id = await service.login()
        
        return {
            "success": True,
            "message": "Successfully authenticated with Smart-1 Cloud",
            "session_id": session_id[:20] + "..." if len(session_id) > 20 else session_id
        }
    except Exception as e:
        logger.error(f"Smart-1 Cloud authentication failed: {_err(e)}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {_err(e)}")


@router.get("/management-services")
async def get_management_services(
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> List[Dict[str, Any]]:
    """
    Get list of management services in Smart-1 Cloud.
    
    Returns list of available management services.
    """
    try:
        services = await service.get_management_services()
        return services
    except Exception as e:
        logger.error(f"Failed to fetch management services: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch management services: {_err(e)}")


@router.post("/gateways")
async def create_gateway(
    request: CreateGatewayRequest,
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> Dict[str, Any]:
    """
    Create a new gateway object in Smart-1 Cloud.
    
    This creates the gateway configuration in the management server.
    """
    try:
        result = await service.create_gateway(
            gateway_name=request.gateway_name,
            sic_otp=request.sic_otp,
            gateway_type=request.gateway_type,
            identification_method=request.identification_method,
            maas_token=request.maas_token,
            version=request.version,
            hardware=request.hardware,
            interfaces=request.interfaces,
            topology_mode=request.topology_mode,
            auto_generate_ip=request.auto_generate_ip
        )
        return result
    except Exception as e:
        logger.error(f"Failed to create gateway: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create gateway: {_err(e)}")


@router.get("/gateways")
async def list_gateways(
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> List[Dict[str, Any]]:
    """
    List all gateways in Smart-1 Cloud.
    
    Returns list of gateway objects.
    """
    try:
        gateways = await service.list_gateways()
        return gateways
    except Exception as e:
        logger.error(f"Failed to list gateways: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list gateways: {_err(e)}")


@router.get("/gateways/{gateway_id}")
async def get_gateway(
    gateway_id: str,
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> Dict[str, Any]:
    """
    Get details of a specific gateway.
    
    Returns gateway configuration and status.
    """
    try:
        gateway = await service.get_gateway(gateway_id)
        return gateway
    except Exception as e:
        logger.error(f"Failed to get gateway: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get gateway: {_err(e)}")


@router.post("/gateways/{gateway_id}/establish-sic")
async def establish_sic(
    gateway_id: str,
    request: EstablishSICRequest,
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> Dict[str, Any]:
    """
    Establish SIC (Secure Internal Communication) with gateway.
    
    This initiates the trust establishment between gateway and management.
    """
    try:
        result = await service.establish_sic(
            gateway_id=gateway_id,
            sic_otp=request.sic_otp
        )
        return result
    except Exception as e:
        logger.error(f"Failed to establish SIC: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to establish SIC: {_err(e)}")


@router.get("/gateways/{gateway_id}/topology")
async def get_gateway_topology(
    gateway_id: str,
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> Dict[str, Any]:
    """
    Fetch gateway topology information.
    
    Returns network interfaces, routes, and configuration.
    """
    try:
        topology = await service.get_gateway_topology(gateway_id)
        return topology
    except Exception as e:
        logger.error(f"Failed to fetch topology: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch topology: {_err(e)}")


@router.get("/gateway-capabilities")
async def get_gateway_capabilities(
    platform: str = None,
    service: Smart1CloudService = Depends(get_smart1_cloud_service)
) -> Dict[str, Any]:
    """
    Get gateway capabilities including supported hardware options.
    
    Args:
        platform: Optional platform filter (smb, quantum, open server, etc.)
    
    Returns:
        Supported hardware, platforms, versions, and blades.
    """
    try:
        capabilities = await service.show_gateway_capabilities(platform=platform)
        return capabilities
    except Exception as e:
        logger.error(f"Failed to fetch gateway capabilities: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch gateway capabilities: {_err(e)}")


@router.get("/hardware-options")
async def get_hardware_options(
    platform: str = None
) -> Dict[str, Any]:
    """
    Get list of supported hardware options for gateway creation.
    
    Args:
        platform: Optional platform filter (smb, quantum, open server, etc.)
    
    Returns:
        List of hardware options suitable for dropdown selection.
    """
    try:
        async with Smart1CloudService() as service:
            await service.login()
            capabilities = await service.show_gateway_capabilities(platform=platform)
        
        # Extract hardware list
        hardware_data = capabilities.get("supported-hardware", {})
        hardware_list = hardware_data.get("hardware", [])
        
        # Extract platforms for reference
        platform_data = capabilities.get("supported-platforms", {})
        platform_list = platform_data.get("platform", [])
        
        return {
            "success": True,
            "hardware": hardware_list,
            "platforms": platform_list,
            "count": len(hardware_list)
        }
    except Exception as e:
        logger.error(f"Failed to fetch hardware options: {_err(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch hardware options: {_err(e)}")
