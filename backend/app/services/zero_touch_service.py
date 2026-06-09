"""
Zero Touch Portal Service
Handles API communication with Check Point Zero Touch Portal
"""
import httpx
import json
from typing import List, Dict, Any, Optional
from loguru import logger
from app.models.zero_touch import LoginResponse, Account, Template, GatewayResponse, GatewayStatus
from app.config import settings
from ..config import log_http_request, log_http_response

class ZeroTouchService:
    """Service for interacting with Check Point Zero Touch Portal API"""

    def __init__(self, base_url: Optional[str] = None, client_id: Optional[str] = None, secret_key: Optional[str] = None):
        self.base_url = (base_url or settings.zero_touch_base_url).rstrip('/')
        self.client_id = client_id or settings.zero_touch_client_id
        self.secret_key = secret_key or settings.zero_touch_secret_key
        self.token = None
        self.client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit — close HTTP client."""
        await self.client.aclose()

    def _get_headers(self) -> Dict[str, str]:
        """Get headers with authentication token"""
        if not self.token:
            raise ValueError("Not authenticated. Please login first.")
        return {
            'X-chkp-sid': self.token,
            'Content-Type': 'application/json'
        }

    def _is_spark_gateway(self, template_name: str) -> bool:
        """Determine if template is for Spark gateway based on name"""
        return template_name and 'spark' in template_name.lower()
    
    async def login(self) -> LoginResponse:
        """Authenticate with Zero Touch Portal"""
        try:
            url = f"{self.base_url}/login"
            payload = {
                'api-client-id': self.client_id,
                'api-key': self.secret_key
            }
            logger.info(f"Attempting login to: {url}")
            logger.debug(f"Client ID: {self.client_id[:10]}...")
            log_http_request(logger.info, "login", payload)
            response = await self.client.post(url, json=payload)
            logger.info(f"Login response status: {response.status_code}")
            log_http_response(logger.info, "login", response.status_code, response.text)

            response.raise_for_status()
            data = response.json()
            self.token = data.get('sid')
            if not self.token:
                logger.error("Login failed: no session ID in response")
                return LoginResponse(success=False, message="Login failed: no session ID")
            logger.info("Login successful")
            return LoginResponse(success=True, message="Authenticated", token=self.token)
        except httpx.HTTPError as e:
            logger.error(f"Login HTTP error: {e}")
            logger.error(f"Response: {getattr(e, 'response', None)}")
            return LoginResponse(success=False, message=f"Login failed: {str(e)}")
        except Exception as e:
            logger.error(f"Login unexpected error: {e}", exc_info=True)
            return LoginResponse(success=False, message=f"Login failed: {str(e)}")
    
    async def get_accounts(self) -> List[Account]:
        """Get all accounts"""
        try:
            url = f"{self.base_url}/show-all-accounts"
            headers = self._get_headers()
            log_http_request(logger.info, "get_accounts", {})
            response = await self.client.post(url, headers=headers)
            log_http_response(logger.info, "get_accounts", response.status_code, response.text)
            response.raise_for_status()
            data = response.json()
            accounts_data = data if isinstance(data, list) else data.get('objects', [])
            return [
                Account(
                    id=str(acc.get('account-id', acc.get('id', ''))),
                    name=acc.get('company-name', acc.get('name', 'Account')),
                    description=acc.get('comments', '')
                )
                for acc in accounts_data
            ]
        except httpx.HTTPError as e:
            logger.error(f"Get accounts failed: {e}")
            raise
    
    async def get_templates(self, account_id: str) -> List[Template]:
        """Get all templates (Spark and Gaia) for an account"""
        try:
            headers = self._get_headers()
            payload = {'account-ids': [int(account_id)]}
            all_templates = []
            
            # Get Spark templates
            try:
                spark_url = f"{self.base_url}/show-all-templates"
                log_http_request(logger.info, "get_templates (Spark)", payload)
                spark_response = await self.client.post(spark_url, headers=headers, json=payload)
                log_http_response(logger.info, "get_templates (Spark)", spark_response.status_code, spark_response.text)
                spark_response.raise_for_status()
                spark_data = spark_response.json()
                spark_templates = spark_data if isinstance(spark_data, list) else spark_data.get('objects', [])
                for t in spark_templates:
                    all_templates.append(Template(
                        id=str(t.get('template-id', t.get('uid', t.get('id', '')))),
                        name=t.get('name', t.get('template-name', 'Unnamed')),
                        description=t.get('comments', ''),
                        version=t.get('version', ''),
                        gateway_type='Spark',
                        template_type='Spark'
                    ))
            except Exception as e:
                logger.warning(f"Could not fetch Spark templates: {e}")
            
            # Get Gaia templates
            try:
                gaia_url = f"{self.base_url}/show-all-gaia-templates"
                log_http_request(logger.info, "get_templates (Gaia)", payload)
                gaia_response = await self.client.post(gaia_url, headers=headers, json=payload)
                log_http_response(logger.info, "get_templates (Gaia)", gaia_response.status_code, gaia_response.text)
                gaia_response.raise_for_status()
                gaia_data = gaia_response.json()
                gaia_templates = gaia_data if isinstance(gaia_data, list) else gaia_data.get('objects', [])
                for t in gaia_templates:
                    all_templates.append(Template(
                        id=str(t.get('template-id', t.get('uid', t.get('id', '')))),
                        name=t.get('name', t.get('template-name', 'Unnamed')),
                        description=t.get('comments', ''),
                        version=t.get('version', ''),
                        gateway_type='Gaia',
                        template_type='Gaia'
                    ))
            except Exception as e:
                logger.warning(f"Could not fetch Gaia templates: {e}")
            
            return all_templates
        except httpx.HTTPError as e:
            logger.error(f"Get templates failed: {e}")
            raise
    
    async def claim_gateway(self, mac_address: str, gateway_name: str, template_id: str, account_id: str, custom_settings: Optional[dict] = None) -> GatewayResponse:
        """Claim a gateway and assign template"""
        try:
            # Get template details to determine the correct endpoint
            templates = await self.get_templates(account_id)
            template = next((t for t in templates if t.id == template_id), None)

            if not template:
                raise ValueError(f"Template {template_id} not found for account {account_id}")

            # Determine endpoint based on template type
            # Use claim-gateway for Spark templates, claim-gaia-gateway for Gaia templates
            is_spark = template.template_type and 'spark' in template.template_type.lower()
            endpoint = '/claim-gateway' if is_spark else '/claim-gaia-gateway'

            url = f"{self.base_url}{endpoint}"
            headers = self._get_headers()
            
            # Build payload with required fields
            payload = {
                'object-name': gateway_name,
                'account-id': int(account_id),
                'template-id': int(template_id),
                'mac': mac_address.upper()  # MAC must be uppercase
            }
            
            # Add optional custom settings
            if custom_settings:
                if custom_settings.get('user_script'):
                    payload['user-script'] = custom_settings['user_script']
                if 'under_construction' in custom_settings:
                    payload['under-construction'] = custom_settings['under_construction']
                if custom_settings.get('time_zone'):
                    payload['time-zone'] = custom_settings['time_zone']
            
            logger.info(f"Claiming gateway at: {url}")
            logger.debug(f"  mac_address={mac_address}, gateway_name={gateway_name}, template_id={template_id}, account_id={account_id}")
            logger.debug(f"  template_name={template.name}, template_type={template.template_type}, is_spark={is_spark}")
            log_http_request(logger.info, "claim_gateway", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "claim_gateway", response.status_code, response.text)
            response.raise_for_status()

            # Handle both list and dict responses
            response_data = response.json()
            if isinstance(response_data, list) and len(response_data) > 0:
                # If API returns a list, use the first element
                response_data = response_data[0]

            return GatewayResponse(success=True, message="Claimed", data=response_data)
        except httpx.HTTPError as e:
            logger.error(f"Claim gateway failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise
    
    async def get_claimed_gateways(self, account_id: str) -> List[Dict[str, Any]]:
        """Get all claimed gateways for an account"""
        try:
            url = f"{self.base_url}/show-all-claimed-gateways"
            headers = self._get_headers()
            payload = {'account-id': int(account_id)}
            log_http_request(logger.info, "get_claimed_gateways", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "get_claimed_gateways", response.status_code, response.text)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else data.get('objects', [])
        except httpx.HTTPError as e:
            logger.error(f"Get claimed gateways failed: {e}")
            raise
    
    async def update_gateway_configuration(self, mac_address: str, account_id: str, template_name: str, settings: dict) -> GatewayResponse:
        """Update gateway configuration"""
        try:
            # Determine endpoint based on template name (Spark vs Gaia)
            is_spark = template_name and 'spark' in template_name.lower()
            endpoint = '/set-claimed-gateway-configuration' if is_spark else '/set-gaia-claimed-gateway-configuration'

            url = f"{self.base_url}{endpoint}"
            headers = self._get_headers()

            # First, get the current configuration to ensure we send all required fields
            logger.info(f"Fetching current configuration before update...")
            try:
                current_config = await self.get_gateway_configuration(mac_address, account_id, template_name)
                logger.debug(f"Current config keys: {list(current_config.keys() if isinstance(current_config, dict) else [])}")
                # Use current config as base
                payload = current_config.copy() if isinstance(current_config, dict) else {}

                # Remove read-only and masked fields that should not be sent back
                readonly_fields = [
                    'creation-time', 'last-modify-time', 'reported-status-time',
                    'activation-url-creation-date', 'activation-url-actuation-time',
                    'creating-user', 'last-modifying-user', 'status-value',
                    'reported-display-status', 'ip-address', 'ext-interface-ip',
                    'is-locked', 'activation-url-key'
                ]
                # Remove masked password fields (API returns "******" which we can't send back)
                masked_password_fields = ['admin-password', 'ftw-sic-key', 'identification-key']

                for field in readonly_fields + masked_password_fields:
                    payload.pop(field, None)

                logger.debug(f"Removed {len(readonly_fields + masked_password_fields)} read-only/masked fields")

            except Exception as e:
                logger.warning(f"Could not fetch current configuration: {e}. Using minimal payload.")
                payload = {}

            # Override with required fields
            payload['account-id'] = int(account_id)
            payload['mac'] = mac_address.upper()

            # Add template info (required for Gaia gateways)
            if settings.get('template_id'):
                payload['template-id'] = int(settings['template_id'])
            if settings.get('template_name'):
                payload['template-name'] = settings['template_name']

            # Add gateway configuration
            if settings.get('hostname'):
                payload['object-name'] = settings['hostname']  # object-name is the gateway name
            # Accept both 'user-script' and 'user_script' formats
            if settings.get('user-script') or settings.get('user_script'):
                user_script = settings.get('user-script') or settings.get('user_script')
                
                # Replace <token> placeholder if maas_token is provided
                maas_token = settings.get('maas_token') or settings.get('maas-token')
                if maas_token and '<token>' in user_script:
                    user_script = user_script.replace('<token>', maas_token)
                    logger.info(f"Replaced <token> placeholder with MaaS token ({len(maas_token)} chars)")
                
                # Replace <sic-key> or <ftw-sic-key> placeholder if sic_otp is provided
                sic_otp = settings.get('sic_otp') or settings.get('sic-otp') or settings.get('ftw-sic-key')
                if sic_otp:
                    if '<sic-key>' in user_script:
                        user_script = user_script.replace('<sic-key>', sic_otp)
                        logger.info("Replaced <sic-key> placeholder for Spark gateway")
                    if '<ftw-sic-key>' in user_script:
                        user_script = user_script.replace('<ftw-sic-key>', sic_otp)
                        logger.info("Replaced <ftw-sic-key> placeholder for Gaia gateway")
                
                payload['user-script'] = user_script
                logger.debug(f"Updated user-script (length: {len(payload['user-script'])})")
            # Accept both 'under-construction' and 'under_construction' formats
            if 'under-construction' in settings or 'under_construction' in settings:
                payload['under-construction'] = settings.get('under-construction', settings.get('under_construction'))
                logger.debug(f"Set under-construction: {payload['under-construction']}")
            if settings.get('time_zone') or settings.get('time-zone'):
                # Keep timezone as-is from settings (API expects exact format from template)
                payload['time-zone'] = settings.get('time_zone') or settings.get('time-zone')
            # For Gaia gateways, handle ftw-sic-key
            if settings.get('ftw-sic-key'):
                payload['ftw-sic-key'] = settings['ftw-sic-key']
                logger.debug("Set ftw-sic-key for Gaia gateway")

            # Pass through all other fields from settings (for Gaia network configuration)
            # These include: dns-server1/2/3, ntp1/2, mgmt-eth-ip-address-ipv4, etc.
            passthrough_fields = [
                'dns-server1', 'dns-server2', 'dns-server3',
                'ntp1', 'ntp1-version', 'ntp2', 'ntp2-version',
                'mgmt-eth-ip-address-ipv4', 'mgmt-eth-subnet-mask-ipv4', 'default-gateway-ipv4',
                'admin-password', 'proxy-server', 'proxy-port',
                'config-ipv6', 'mgmt-eth-ip-address-ipv6', 'mgmt-eth-mask-length-ipv6', 'default-gateway-ipv6',
                'upload-info', 'download-info'
            ]

            # Fields that should be omitted if empty (not sent as empty string)
            optional_fields = ['proxy-server', 'admin-password']

            for field in passthrough_fields:
                if field in settings:
                    value = settings[field]
                    # Skip optional fields if they're empty strings
                    if field in optional_fields and value == '':
                        logger.debug(f"Skipping empty optional field: {field}")
                        continue
                    payload[field] = value
                    logger.debug(f"Added {field}: {value}")

            logger.info(f"Updating gateway configuration at: {url}")
            logger.info(f"MAC Address (uppercase): {mac_address.upper()}, template_name={template_name}, is_spark={is_spark}")
            log_http_request(logger.info, "update_gateway_configuration", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "update_gateway_configuration", response.status_code, response.text)
            response.raise_for_status()

            return GatewayResponse(success=True, message="Configuration updated", data=response.json())
        except httpx.HTTPError as e:
            logger.error(f"Update gateway configuration failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise

    async def get_gateway_configuration(self, mac_address: str, account_id: str, template_name: str) -> Dict[str, Any]:
        """Get gateway configuration (user-script, etc.)"""
        try:
            # Determine endpoint based on template name
            is_spark = template_name and 'spark' in template_name.lower()
            endpoint = '/show-claimed-gateway-configuration' if is_spark else '/show-gaia-claimed-gateway-configuration'

            url = f"{self.base_url}{endpoint}"
            headers = self._get_headers()
            payload = {'account-id': int(account_id), 'mac': mac_address.upper()}

            logger.info(f"Getting gateway configuration from: {url}")
            log_http_request(logger.info, "get_gateway_configuration", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "get_gateway_configuration", response.status_code, response.text)
            response.raise_for_status()

            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Get gateway configuration failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
                # Re-raise with the actual error message from ZT
                try:
                    error_detail = e.response.json()
                    # ZT error responses can use: message, error, or messages[]
                    messages = error_detail.get('messages')
                    if messages and isinstance(messages, list):
                        msg = '; '.join(
                            m.get('message', str(m)) if isinstance(m, dict) else str(m)
                            for m in messages
                        )
                    else:
                        msg = (error_detail.get('message')
                               or error_detail.get('error')
                               or e.response.text)
                    raise Exception(f"Zero Touch {e.response.status_code}: {msg}") from e
                except (ValueError, KeyError):
                    raise Exception(f"Zero Touch {e.response.status_code}: {e.response.text}") from e
            raise

    async def get_gateway_status(self, mac_address: str, account_id: str, retry_on_not_found: bool = True) -> Dict[str, Any]:
        """Get gateway status"""
        try:
            url = f"{self.base_url}/show-gaia-claimed-gateway"
            headers = self._get_headers()
            payload = {'account-id': int(account_id), 'mac': mac_address.upper()}
            log_http_request(logger.info, "get_gateway_status", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "get_gateway_status", response.status_code, response.text)
            response.raise_for_status()

            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Get gateway status failed: {e}")
            raise

    async def get_gateway_deployment_status(self, mac_address: str, account_id: str, is_spark: bool = False) -> Dict[str, Any]:
        """
        Get gateway deployment status from Zero Touch Portal.

        Uses show-claimed-gateway-status for Spark gateways and
        show-gaia-claimed-gateway-status for Gaia gateways.

        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            is_spark: True for Spark gateways, False for Gaia gateways

        Returns:
            Dict with status info:
            - mac: Gateway MAC address
            - reported-status-time: Timestamp of last status report (milliseconds since epoch)
            - reported-display-status: Status string - one of:
                "Not reported", "Installing", "Finished", "Rebooting", "Failed", "Error", "Fetched"
        """
        try:
            endpoint = '/show-claimed-gateway-status' if is_spark else '/show-gaia-claimed-gateway-status'
            url = f"{self.base_url}{endpoint}"
            headers = self._get_headers()
            payload = {'account-id': int(account_id), 'mac': mac_address.upper()}

            logger.info(f"Checking deployment status at: {url}")
            log_http_request(logger.info, "get_gateway_deployment_status", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "get_gateway_deployment_status", response.status_code, response.text)
            response.raise_for_status()

            status_data = response.json()
            logger.info(f"Deployment status: {status_data.get('reported-display-status', 'Unknown')}")
            
            return status_data
        except httpx.HTTPError as e:
            logger.error(f"Get gateway deployment status failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise

    async def unclaim_gateway(self, mac_address: str, account_id: str, is_spark: bool = False) -> GatewayResponse:
        """Unclaim a gateway"""
        try:
            # Use correct endpoints based on gateway type
            # Spark: unclaim-gateway
            # Gaia: unclaim-gaia-gateway
            endpoint = '/unclaim-gateway' if is_spark else '/unclaim-gaia-gateway'
            url = f"{self.base_url}{endpoint}"
            headers = self._get_headers()
            payload = {'account-id': int(account_id), 'mac': mac_address.upper()}

            logger.info(f"Unclaiming gateway at: {url}")
            log_http_request(logger.info, "unclaim_gateway", payload)
            response = await self.client.post(url, headers=headers, json=payload)
            log_http_response(logger.info, "unclaim_gateway", response.status_code, response.text)
            response.raise_for_status()

            return GatewayResponse(success=True, message="Unclaimed", data=response.json())
        except httpx.HTTPError as e:
            logger.error(f"Unclaim gateway failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise

    async def unmark_under_construction(self, mac_address: str, account_id: str) -> GatewayResponse:
        """
        Remove the 'under construction' flag from a gateway.
        
        This marks the gateway as ready for deployment/use.
        
        Args:
            mac_address: Gateway MAC address
            account_id: Zero Touch account ID
            
        Returns:
            GatewayResponse indicating success/failure
        """
        try:
            logger.info(f"Unmarking gateway {mac_address.upper()} as under-construction")
            
            # Use update_gateway_configuration to set under-construction to false
            # We need template_name - try to get it from current gateway config
            # First try Spark endpoint, then Gaia
            template_name = None
            
            for endpoint in ['/show-claimed-gateway-configuration', '/show-gaia-claimed-gateway-configuration']:
                try:
                    url = f"{self.base_url}{endpoint}"
                    headers = self._get_headers()
                    payload = {'account-id': int(account_id), 'mac': mac_address.upper()}
                    
                    log_http_request(logger.debug, endpoint.lstrip('/'), payload)
                    response = await self.client.post(url, headers=headers, json=payload)
                    log_http_response(logger.debug, endpoint.lstrip('/'), response.status_code, response.text)
                    if response.status_code == 200:
                        config = response.json()
                        template_name = config.get('template-name')
                        if template_name:
                            logger.info(f"Found template name: {template_name}")
                            break
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
                    continue
            
            if not template_name:
                raise ValueError("Could not determine template name for gateway")
            
            # Update configuration to remove under-construction flag
            result = await self.update_gateway_configuration(
                mac_address=mac_address,
                account_id=account_id,
                template_name=template_name,
                settings={'under_construction': False}
            )
            
            logger.info(f"Successfully unmarked gateway {mac_address.upper()} as under-construction")
            return result
            
        except Exception as e:
            logger.error(f"Unmark under-construction failed: {e}")
            raise
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
