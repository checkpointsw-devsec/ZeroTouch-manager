"""
Pydantic models for Zero Touch Portal API.
Based on: https://sc1.checkpoint.com/documents/Appliances/Zero_Touch_REST_API_Guide/
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class LoginRequest(BaseModel):
    """Request model for Zero Touch Portal login."""
    clientId: str = Field(..., description="Client ID for Zero Touch Portal")
    accessKey: str = Field(..., description="Secret access key for Zero Touch Portal")


class LoginResponse(BaseModel):
    """Response model for Zero Touch Portal login."""
    success: bool = Field(default=True, description="Whether login was successful")
    message: str = Field(default="", description="Response message")
    token: Optional[str] = Field(None, description="Authentication token (SID)")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional response data")


class Account(BaseModel):
    """Model representing a Zero Touch Portal account."""
    id: str = Field(..., description="Account ID")
    name: str = Field(..., description="Account name")
    description: Optional[str] = Field(None, description="Account description")
    type: Optional[str] = Field(None, description="Account type")


class TemplateInterface(BaseModel):
    """Model for template network interface configuration."""
    name: Optional[str] = None
    ipv4_address: Optional[str] = None
    ipv4_mask: Optional[str] = None
    ipv6_address: Optional[str] = None
    ipv6_prefix: Optional[int] = None
    enabled: Optional[bool] = True


class Template(BaseModel):
    """Model representing a Zero Touch Portal template."""
    id: str = Field(..., description="Template ID")
    name: str = Field(..., description="Template name")
    description: Optional[str] = Field(None, description="Template description")
    gateway_type: Optional[str] = Field(None, description="Gateway type (Quantum Force, Quantum Spark)")
    version: Optional[str] = Field(None, description="Gateway version")
    account_id: Optional[str] = Field(None, description="Associated account ID")
    template_type: Optional[str] = Field(None, description="Template type (Spark, Gaia, Gaia Embedded)")
    
    hostname: Optional[str] = None
    mgmt_interface: Optional[str] = None
    mgmt_ipv4: Optional[str] = None
    mgmt_ipv4_mask: Optional[str] = None
    mgmt_ipv6: Optional[str] = None
    mgmt_ipv6_prefix: Optional[int] = None
    default_gateway: Optional[str] = None
    interfaces: Optional[List[TemplateInterface]] = None
    dns_primary: Optional[str] = None
    dns_secondary: Optional[str] = None
    ntp_server: Optional[str] = None
    timezone: Optional[str] = None


class ClaimGatewayRequest(BaseModel):
    """Request model for claiming a gateway MAC address."""
    mac_address: str = Field(..., description="Gateway MAC address to claim")
    template_id: str = Field(..., description="Template ID to use for provisioning")
    gateway_name: str = Field(..., description="Name for the gateway")
    account_id: str = Field(..., description="Account ID to assign gateway to")
    custom_settings: Optional[Dict[str, Any]] = Field(None, description="Custom settings to override template defaults")


class GatewaySettings(BaseModel):
    """Model for gateway configuration settings."""
    hostname: Optional[str] = Field(None, description="Gateway hostname")
    mgmt_ipv4: Optional[str] = Field(None, description="Management IPv4 address")
    mgmt_ipv4_mask: Optional[str] = Field(None, description="Management IPv4 subnet mask")
    mgmt_ipv6: Optional[str] = Field(None, description="Management IPv6 address")
    mgmt_ipv6_prefix: Optional[int] = Field(None, description="Management IPv6 prefix length")
    default_gateway: Optional[str] = Field(None, description="Default gateway IP")
    dns_primary: Optional[str] = Field(None, description="Primary DNS server")
    dns_secondary: Optional[str] = Field(None, description="Secondary DNS server")
    ntp_server: Optional[str] = Field(None, description="NTP server")
    timezone: Optional[str] = Field(None, description="Timezone")
    interfaces: Optional[List[TemplateInterface]] = Field(None, description="Additional network interfaces configuration")
    enable_ssh: Optional[bool] = Field(True, description="Enable SSH access")
    enable_https: Optional[bool] = Field(True, description="Enable HTTPS access")
    under_construction: Optional[bool] = Field(None, description="Mark gateway as under construction")
    user_script: Optional[str] = Field(None, description="Custom user script")


class GatewayStatus(BaseModel):
    """Model representing the status of a claimed gateway."""
    mac_address: str = Field(..., description="Gateway MAC address")
    gateway_name: str = Field(..., description="Gateway name/object name")
    hostname: Optional[str] = Field(None, description="Gateway hostname")
    under_construction: bool = Field(default=False, description="Whether gateway is under construction")
    user_script: Optional[str] = Field(None, description="User script/CLISH script")
    timezone: Optional[str] = Field(None, description="Gateway timezone")
    template_id: Optional[str] = Field(None, description="Applied template ID")
    account_id: Optional[str] = Field(None, description="Associated account ID")
    status: Optional[str] = Field(None, description="Gateway status")
    last_seen: Optional[datetime] = Field(None, description="Last seen timestamp")


class GatewayResponse(BaseModel):
    """Generic response model for gateway operations."""
    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Response message")
    data: Optional[Dict[str, Any]] = Field(None, description="Response data")


class UpdateGatewayRequest(BaseModel):
    """Request model for updating gateway configuration."""
    mac_address: str = Field(..., description="Gateway MAC address")
    account_id: str = Field(..., description="Account ID")
    settings: Dict[str, Any] = Field(..., description="Gateway settings to update")


class UnmarkConstructionRequest(BaseModel):
    """Request model for unmarking a gateway as under construction."""
    account_id: str = Field(..., description="Account ID")
