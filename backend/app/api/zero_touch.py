from fastapi import APIRouter, HTTPException, Depends, Body
from typing import List, Dict, Any, Optional
from loguru import logger

from ..models.zero_touch import (
    LoginResponse,
    Account,
    Template,
    ClaimGatewayRequest,
    UpdateGatewayRequest,
    UnmarkConstructionRequest,
    GatewayResponse,
    GatewayStatus
)
from ..services.zero_touch_service import ZeroTouchService
from ..services.script_utils import apply_script_substitutions

router = APIRouter(prefix="/api/zero-touch", tags=["Zero Touch Portal"])

# Module-level singleton — keeps the session token alive across requests.
# (Orchestrators create their own short-lived instances via ``async with``.)
_zt_service = ZeroTouchService()


async def get_zero_touch_service():
    """Dependency that returns the shared ZeroTouchService singleton."""
    return _zt_service


@router.post("/login", response_model=LoginResponse)
async def login(service: ZeroTouchService = Depends(get_zero_touch_service)) -> LoginResponse:
    """
    Login to Zero Touch Portal and obtain authentication token.

    This is the first step in the deployment workflow.
    """
    try:
        response = await service.login()
        return response
    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Login failed: {str(e)}")


@router.get("/accounts", response_model=List[Account])
async def get_accounts(service: ZeroTouchService = Depends(get_zero_touch_service)) -> List[Account]:
    """
    Get list of accessible accounts.

    Returns all accounts accessible with the current credentials.
    """
    try:
        accounts = await service.get_accounts()
        return accounts
    except ValueError as e:
        # Not authenticated
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch accounts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch accounts: {str(e)}")


@router.get("/templates", response_model=List[Template])
async def get_templates(
    account_id: Optional[str] = None,
    service: ZeroTouchService = Depends(get_zero_touch_service)
) -> List[Template]:
    """
    Get list of available templates.

    Args:
        account_id: Optional account ID to filter templates
    """
    try:
        templates = await service.get_templates(account_id=account_id)
        return templates
    except ValueError as e:
        # Not authenticated
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch templates: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch templates: {str(e)}")


@router.post("/gateways/claim", response_model=GatewayResponse)
async def claim_gateway(
    request: ClaimGatewayRequest,
    service: ZeroTouchService = Depends(get_zero_touch_service)
) -> GatewayResponse:
    """
    Claim a gateway MAC address and assign it to a template.

    This step provisions the gateway with the selected template configuration.
    """
    try:
        response = await service.claim_gateway(
            mac_address=request.mac_address,
            template_id=request.template_id,
            gateway_name=request.gateway_name,
            account_id=request.account_id,
            custom_settings=request.custom_settings
        )
        # Apply ##!! file injection to user-script in gateway-configuration
        if response.data:
            if "gateway-configuration" in response.data:
                # Spark response: config nested under gateway-configuration
                gw_config = response.data["gateway-configuration"]
                user_script = gw_config.get("user-script", "")
                gateway_name = gw_config.get("object-name", request.gateway_name)
                if user_script:
                    gw_config = dict(gw_config)
                    gw_config["user-script"] = apply_script_substitutions(user_script, gateway_name)
                    response = response.model_copy(update={"data": {**response.data, "gateway-configuration": gw_config}})
            elif "user-script" in response.data:
                # Gaia response: config at top level
                user_script = response.data.get("user-script", "")
                gateway_name = response.data.get("object-name", request.gateway_name)
                if user_script:
                    updated = dict(response.data)
                    updated["user-script"] = apply_script_substitutions(user_script, gateway_name)
                    response = response.model_copy(update={"data": updated})
        return response
    except ValueError as e:
        # Not authenticated or template not found
        logger.error(f"ValueError in claim_gateway: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to claim gateway: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to claim gateway: {str(e)}")


@router.post("/gateways/{mac_address}/ready")
async def mark_gateway_ready(
    mac_address: str,
    request: UnmarkConstructionRequest,
    service: ZeroTouchService = Depends(get_zero_touch_service)
):
    """
    Mark a gateway as ready for use (remove 'under construction' status).

    This is the final step before the gateway can be deployed.
    """
    try:
        response = await service.unmark_under_construction(
            mac_address=mac_address,
            account_id=request.account_id
        )
        return response
    except ValueError as e:
        # Not authenticated
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to mark gateway as ready: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to mark gateway as ready: {str(e)}")


@router.get("/gateways/{mac_address}/status", response_model=GatewayStatus)
async def get_gateway_status(
    mac_address: str,
    account_id: str,
    service: ZeroTouchService = Depends(get_zero_touch_service)
) -> GatewayStatus:
    """
    Get current status of a gateway.

    Returns the current deployment status and configuration of the gateway.
    """
    try:
        status = await service.get_gateway_status(mac_address=mac_address, account_id=account_id)
        return status
    except ValueError as e:
        # Not authenticated
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch gateway status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch gateway status: {str(e)}")



@router.get("/gateways/{mac_address}/configuration")
async def get_gateway_configuration(
    mac_address: str,
    account_id: str,
    template_name: str,
    service: ZeroTouchService = Depends(get_zero_touch_service)
):
    """
    Get gateway configuration.

    Uses show-claimed-gateway-configuration for Spark templates.
    Uses show-gaia-claimed-gateway-configuration for Gaia templates.
    """
    try:
        # Login first
        await service.login()

        # Get gateway configuration
        config = await service.get_gateway_configuration(
            mac_address=mac_address,
            account_id=account_id,
            template_name=template_name
        )

        # Apply ##!! file injection and <gateway-name> substitution to user-script
        user_script = config.get("user-script", "")
        if user_script:
            gateway_name = config.get("object-name", "")
            config = dict(config)
            config["user-script"] = apply_script_substitutions(user_script, gateway_name)

        return {
            "success": True,
            "data": config
        }

    except ValueError as e:
        # Not authenticated
        raise HTTPException(status_code=401, detail=str(e))
    except httpx.HTTPError as e:
        logger.error(f"Failed to get gateway configuration: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get gateway configuration: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to get gateway configuration: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get gateway configuration: {str(e)}")


@router.put("/gateways/{mac_address}/configuration")
async def update_gateway_configuration(
    mac_address: str,
    account_id: str,
    template_name: str,
    request_body: Dict[str, Any] = Body(...),
    service: ZeroTouchService = Depends(get_zero_touch_service)
):
    """
    Update gateway configuration.

    Uses set-claimed-gateway-configuration for Spark templates.
    Uses set-gaia-claimed-gateway-configuration for Gaia templates.
    """
    try:
        # Extract settings from request body
        # Frontend sends all fields including Gaia network configuration
        # Pass through all fields to the service (it will handle conversion)
        logger.info(f"Received update request for {mac_address} with fields: {list(request_body.keys())}")

        result = await service.update_gateway_configuration(
            mac_address=mac_address,
            account_id=account_id,
            template_name=template_name,
            settings=request_body  # Pass through all fields from frontend
        )
        return result
    except Exception as e:
        logger.error(f"Failed to update gateway configuration: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/gateways/{mac_address}")
async def unclaim_gateway(
    mac_address: str,
    account_id: str,
    template_name: str = None,
    service: ZeroTouchService = Depends(get_zero_touch_service)
) -> Dict[str, Any]:
    """
    Unclaim a gateway from the Zero Touch Portal.

    Args:
        mac_address: Gateway MAC address
        account_id: Account ID
        template_name: Optional template name to determine Spark vs Gaia
    """
    try:
        # Determine if Spark based on template name
        is_spark = template_name and 'spark' in template_name.lower()

        response = await service.unclaim_gateway(
            mac_address=mac_address,
            account_id=account_id,
            is_spark=is_spark
        )
        # Convert GatewayResponse to dict for API response
        return {
            "success": response.success,
            "message": response.message,
            "data": response.data
        }
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to unclaim gateway: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to unclaim gateway: {str(e)}")
