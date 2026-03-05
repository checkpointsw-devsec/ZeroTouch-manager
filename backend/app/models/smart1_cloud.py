"""
Pydantic models for Smart-1 Cloud MaaS API.
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum


class GatewayType(str, Enum):
    """Gateway type enumeration."""
    APPLIANCE_OR_OPENSERVER = "APPLIANCE_OR_OPENSERVER"
    VMWARE = "VMWARE"
    AZURE = "AZURE"
    AWS = "AWS"
    GCP = "GCP"


class IdentificationMethod(str, Enum):
    """Device identification method."""
    GATEWAY_NAME = "GATEWAY_NAME"
    IP_ADDRESS = "IP_ADDRESS"
    MAC_ADDRESS = "MAC_ADDRESS"


class CreateGatewayRequest(BaseModel):
    """Request model for creating a gateway in Smart-1 Cloud."""
    gateway_name: str = Field(..., description="Name for the gateway")
    sic_otp: str = Field(..., description="Secure Internal Communication one-time password")
    gateway_type: GatewayType = Field(
        default=GatewayType.APPLIANCE_OR_OPENSERVER,
        description="Type of gateway"
    )
    identification_method: IdentificationMethod = Field(
        default=IdentificationMethod.GATEWAY_NAME,
        description="How to identify the gateway"
    )
    maas_token: Optional[str] = Field(None, description="MaaS authentication token")
    version: Optional[str] = Field(None, description="Gateway version")
    hardware: Optional[str] = Field(None, description="Gateway hardware type")
    interfaces: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="List of gateway interfaces")
    topology_mode: Optional[str] = Field(None, description="Gateway topology mode")
    auto_generate_ip: Optional[bool] = Field(False, description="Auto generate IP for gateway (DAIP)")


class EstablishSICRequest(BaseModel):
    """Request model for establishing SIC."""
    gateway_id: str = Field(..., description="Gateway ID")
    sic_otp: str = Field(..., description="One-time password for SIC establishment")


class ManagementService(BaseModel):
    """Management service information."""
    id: str
    name: str
    description: Optional[str] = None
    type: Optional[str] = None


class Gateway(BaseModel):
    """Gateway object model."""
    id: str
    name: str
    type: Optional[str] = None
    status: Optional[str] = None
    sic_status: Optional[str] = None
    ip_address: Optional[str] = None
    version: Optional[str] = None
    last_connected: Optional[str] = None


class GatewayTopology(BaseModel):
    """Gateway topology information."""
    gateway_id: str
    interfaces: Optional[List[Dict[str, Any]]] = []
    routes: Optional[List[Dict[str, Any]]] = []
    nat_rules: Optional[List[Dict[str, Any]]] = []
