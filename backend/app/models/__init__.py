"""
Pydantic models for the Check Point Gateway Deployer application.
"""
from .zero_touch import (
    LoginRequest,
    LoginResponse,
    Account,
    Template,
    ClaimGatewayRequest,
    GatewaySettings,
    GatewayResponse
)

from .smart1_cloud import (
    GatewayType,
    IdentificationMethod,
    CreateGatewayRequest,
    EstablishSICRequest,
    ManagementService,
    Gateway,
    GatewayTopology
)

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "Account",
    "Template",
    "ClaimGatewayRequest",
    "GatewaySettings",
    "GatewayResponse",
    "GatewayType",
    "IdentificationMethod",
    "CreateGatewayRequest",
    "EstablishSICRequest",
    "ManagementService",
    "Gateway",
    "GatewayTopology",
]
