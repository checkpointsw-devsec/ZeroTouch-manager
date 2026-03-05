#!/usr/bin/env python3
"""
Batch Gateway Deployment Script

Deploy multiple gateways from a CSV file using the Gateway Deployer API.
Supports Smart-1 Cloud, LSM, SMS, and SMP deployment flows.

Each deployment type uses a separate CSV file with only the required fields.

Usage:
    python deploy-batch.py --api-url http://localhost:8000 --deployment-type s1c --csv sample_smart1_cloud.csv  --filter gw-1590w-01
    python deploy-batch.py --api-url http://localhost:8000 --deployment-type sms          --csv sample_sms.csv           --filter gw-1590w-01
    python deploy-batch.py --api-url http://localhost:8000 --deployment-type smp          --csv sample_smp.csv           --filter gw-1590w-01
    python deploy-batch.py --api-url http://localhost:8000 --deployment-type lsm          --csv sample_lsm.csv           --filter gw-1590w-01
    
CSV Formats:
    Smart-1 Cloud (Gaia gateways):
        mac_address, account_id, template_id, template_name, gateway_name, sic_otp,
        user_script (optional), time_zone (optional), hardware (optional: e.g. 'Check Point 6200'),
        gateway_type (optional), identification_method (optional),
        os_version (optional: R81.10 or R82),
        firewall (optional: true/false, default: true),
        vpn (optional: true/false, default: true),
        ips (optional: true/false, default: true),
        application_control (optional: true/false, default: true),
        url_filtering (optional: true/false, default: true),
        anti_bot (optional: true/false, default: true),
        anti_virus (optional: true/false, default: true),
        threat_emulation (optional: true/false, default: true),
        policy_name (optional: policy package to install after deployment),
        vpn_community (optional: VPN community name to add gateway to),
        vpn_role (optional: 'center' or 'satellite', default: satellite)
    
    LSM (Spark gateways only):
        mac_address, account_id, template_id, template_name, gateway_name,
        sic_otp, security_profile, provisioning_profile,
        domain (optional)
        Note: mgmt_server_ip is taken from MGMT_SERVER_HOST in .env if not provided
    
    SMS (Gaia gateways):
        mac_address, account_id, template_name, gateway_name, mgmt_server_ip, sic_otp,
        gateway_ipv4, version, hardware,
        policy_name (optional), enable_app_control (optional), enable_ips (optional),
        enable_url_filtering (optional), enable_content_awareness (optional),
        enable_ipsec (optional), vpn_community (optional), vpn_role (optional), domain (optional)

    SMP (Spark gateways, Zero Touch only — no management server):
        mac_address, account_id, template_id, template_name, gateway_name
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx")
    sys.exit(1)


class DeploymentType(Enum):
    SMART1_CLOUD = "s1c"
    LSM = "lsm"
    SMS = "sms"
    SMP = "smp"


@dataclass
class DeploymentResult:
    """Result of a single gateway deployment."""
    gateway_name: str
    mac_address: str
    deployment_type: str
    success: bool
    message: str
    activation_link: Optional[str] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None


class BatchDeployer:
    """Batch gateway deployment using the Gateway Deployer API."""
    
    def __init__(self, api_url: str, timeout: int = 600, verbose: bool = False):
        """
        Initialize the batch deployer.
        
        Args:
            api_url: Base URL of the Gateway Deployer API (e.g., http://localhost:8000)
            timeout: Request timeout in seconds (default 600 for long deployments)
            verbose: Enable verbose output
        """
        self.api_url = api_url.rstrip('/')
        self.timeout = timeout
        self.verbose = verbose
        self.client = httpx.Client(timeout=timeout, verify=False)
        self.results: List[DeploymentResult] = []
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client.close()
        
    def log(self, message: str, level: str = "INFO"):
        """Log a message with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}", flush=True)
        
    def verbose_log(self, message: str):
        """Log verbose message if verbose mode is enabled."""
        if self.verbose:
            self.log(message, "DEBUG")
            
    def _stream_deployment(self, url: str, payload: Dict[str, Any], gateway_name: str, mac_address: str, deployment_type: str) -> DeploymentResult:
        """
        Call a streaming SSE deployment endpoint and print each status line as it arrives.
        Returns a DeploymentResult when the stream completes.
        """
        start_time = time.time()
        final_result = None
        last_step = None

        try:
            with self.client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("event")
                    data = event.get("data", {})

                    if event_type == "status":
                        step = data.get("step", "")
                        message = data.get("message", "")
                        status = data.get("status", "in_progress")
                        step_str = f"[Step {step}] " if step != last_step else "           "
                        last_step = step
                        level = "SUCCESS" if status == "completed" else "INFO"
                        self.log(f"  {step_str}{message}", level)

                    elif event_type == "complete":
                        final_result = data
                    elif event_type == "error":
                        error_msg = data.get("error", "Unknown error")
                        self.log(f"[FAIL] {gateway_name} stream error: {error_msg}", "ERROR")
                        duration = time.time() - start_time
                        return DeploymentResult(
                            gateway_name=gateway_name,
                            mac_address=mac_address,
                            deployment_type=deployment_type,
                            success=False,
                            message="Deployment error",
                            error=error_msg,
                            duration_seconds=duration
                        )
                    # heartbeat — ignore

        except httpx.HTTPStatusError as e:
            duration = time.time() - start_time
            error_detail = e.response.text if e.response else str(e)
            self.log(f"[FAIL] {gateway_name} HTTP error: {error_detail}", "ERROR")
            return DeploymentResult(
                gateway_name=gateway_name,
                mac_address=mac_address,
                deployment_type=deployment_type,
                success=False,
                message="HTTP error",
                error=error_detail,
                duration_seconds=duration
            )
        except Exception as e:
            duration = time.time() - start_time
            self.log(f"[FAIL] {gateway_name} error: {str(e)}", "ERROR")
            return DeploymentResult(
                gateway_name=gateway_name,
                mac_address=mac_address,
                deployment_type=deployment_type,
                success=False,
                message="Unexpected error",
                error=str(e),
                duration_seconds=duration
            )

        duration = time.time() - start_time
        if final_result and final_result.get('success'):
            self.log(f"[OK] {gateway_name} deployed successfully in {duration:.1f}s", "SUCCESS")
            return DeploymentResult(
                gateway_name=gateway_name,
                mac_address=mac_address,
                deployment_type=deployment_type,
                success=True,
                message="Deployment successful",
                activation_link=final_result.get('activation_link'),
                duration_seconds=duration
            )
        else:
            error = (final_result or {}).get('error', 'No result received')
            self.log(f"[FAIL] {gateway_name} deployment failed: {error}", "ERROR")
            return DeploymentResult(
                gateway_name=gateway_name,
                mac_address=mac_address,
                deployment_type=deployment_type,
                success=False,
                message="Deployment failed",
                error=error,
                duration_seconds=duration
            )

    def deploy_smart1_cloud(self, row: Dict[str, str]) -> DeploymentResult:
        """Deploy a gateway to Smart-1 Cloud."""
        gateway_name = row.get('gateway_name', 'Unknown')
        mac_address = row.get('mac_address', '')

        self.log(f"Deploying {gateway_name} ({mac_address}) to Smart-1 Cloud...")

        def parse_bool(value: str, default: bool = False) -> bool:
            if not value:
                return default
            return value.lower() in ('true', '1', 'yes', 'on')

        policy_name = row.get('policy_name', '')
        vpn_community = row.get('vpn_community', '')
        vpn_role = row.get('vpn_role', 'satellite')

        payload = {
            "mac_address": row['mac_address'],
            "account_id": row['account_id'],
            "template_id": row['template_id'],
            "template_name": row['template_name'],
            "gateway_name": row['gateway_name'],
            "user_script": row.get('user_script', ''),
            "time_zone": row.get('time_zone', 'UTC'),
            "sic_otp": row['sic_otp'],
            "hardware": row.get('hardware', '').strip() or None,
            "gateway_type": row.get('gateway_type', 'APPLIANCE_OR_OPENSERVER'),
            "identification_method": row.get('identification_method', 'GATEWAY_NAME'),
            "os_version": row.get('os_version', 'R81.10'),
            "firewall": parse_bool(row.get('firewall'), True),
            "vpn": parse_bool(row.get('vpn'), True),
            "ips": parse_bool(row.get('ips'), True),
            "application_control": parse_bool(row.get('application_control'), True),
            "url_filtering": parse_bool(row.get('url_filtering'), True),
            "anti_bot": parse_bool(row.get('anti_bot'), True),
            "anti_virus": parse_bool(row.get('anti_virus'), True),
            "threat_emulation": parse_bool(row.get('threat_emulation'), True),
            "policy_name": policy_name.strip() if policy_name.strip() else None,
            "vpn_community": vpn_community.strip() if vpn_community.strip() else None,
            "vpn_role": vpn_role.strip() if vpn_community.strip() else "satellite",
            "ipv4_address": row.get('ipv4_address', '').strip() or None
        }

        self.verbose_log(f"Payload: {json.dumps(payload, indent=2)}")

        return self._stream_deployment(
            url=f"{self.api_url}/api/deployment/deploy-with-smart1-cloud/stream",
            payload=payload,
            gateway_name=gateway_name,
            mac_address=mac_address,
            deployment_type="s1c"
        )
            
    def deploy_lsm(self, row: Dict[str, str]) -> DeploymentResult:
        """Deploy a gateway to LSM."""
        gateway_name = row.get('gateway_name', 'Unknown')
        mac_address = row.get('mac_address', '')

        self.log(f"Deploying {gateway_name} ({mac_address}) to LSM...")

        payload = {
            "mac_address": row['mac_address'],
            "account_id": row['account_id'],
            "template_name": row['template_name'],
            "gateway_name": row['gateway_name'],
        }

        if row.get('template_id'):
            payload['template_id'] = row['template_id']
        if row.get('mgmt_server_ip'):
            payload['mgmt_server_ip'] = row['mgmt_server_ip']
        if row.get('sic_otp'):
            payload['sic_otp'] = row['sic_otp']
        if row.get('gateway_ipv4'):
            payload['gateway_ipv4'] = row['gateway_ipv4']
        if row.get('security_profile'):
            payload['security_profile'] = row['security_profile']
        if row.get('provisioning_profile'):
            payload['provisioning_profile'] = row['provisioning_profile']
        if row.get('domain'):
            payload['domain'] = row['domain']

        self.verbose_log(f"Payload: {json.dumps(payload, indent=2)}")

        return self._stream_deployment(
            url=f"{self.api_url}/api/deployment/deploy-with-lsm/stream",
            payload=payload,
            gateway_name=gateway_name,
            mac_address=mac_address,
            deployment_type="lsm"
        )
            
    def deploy_sms(self, row: Dict[str, str]) -> DeploymentResult:
        """Deploy a gateway to SMS."""
        gateway_name = row.get('gateway_name', 'Unknown')
        mac_address = row.get('mac_address', '')

        self.log(f"Deploying {gateway_name} ({mac_address}) to SMS...")

        def parse_bool(value: str, default: bool = False) -> bool:
            if not value:
                return default
            return value.lower() in ('true', '1', 'yes', 'on')

        payload = {
            "mac_address": row['mac_address'],
            "account_id": row['account_id'],
            "template_name": row['template_name'],
            "gateway_name": row['gateway_name'],
            "mgmt_server_ip": row['mgmt_server_ip'],
            "sic_otp": row['sic_otp'],
            "gateway_ipv4": row['gateway_ipv4'],
            "version": row['version'],
            "hardware": row['hardware'],
            "policy_name": row.get('policy_name', 'Standard'),
            "enable_app_control": parse_bool(row.get('enable_app_control'), True),
            "enable_ips": parse_bool(row.get('enable_ips'), True),
            "enable_url_filtering": parse_bool(row.get('enable_url_filtering'), False),
            "enable_content_awareness": parse_bool(row.get('enable_content_awareness'), False),
            "enable_ipsec": parse_bool(row.get('enable_ipsec'), True),
            "vpn_role": row.get('vpn_role', 'satellite')
        }

        if row.get('template_id'):
            payload['template_id'] = row['template_id']
        if row.get('vpn_community'):
            payload['vpn_community'] = row['vpn_community']
        if row.get('domain'):
            payload['domain'] = row['domain']

        self.verbose_log(f"Payload: {json.dumps(payload, indent=2)}")

        return self._stream_deployment(
            url=f"{self.api_url}/api/deployment/deploy-with-sms/stream",
            payload=payload,
            gateway_name=gateway_name,
            mac_address=mac_address,
            deployment_type="sms"
        )

    def deploy_smp(self, row: Dict[str, str]) -> DeploymentResult:
        """Deploy a Spark gateway to SMP (Zero Touch only — no management server)."""
        gateway_name = row.get('gateway_name', 'Unknown')
        mac_address = row.get('mac_address', '')

        self.log(f"Deploying {gateway_name} ({mac_address}) to SMP...")

        payload = {
            "mac_address": row['mac_address'],
            "account_id": row['account_id'],
            "template_id": row['template_id'],
            "template_name": row['template_name'],
            "gateway_name": row['gateway_name'],
        }

        self.verbose_log(f"Payload: {json.dumps(payload, indent=2)}")

        return self._stream_deployment(
            url=f"{self.api_url}/api/deployment/deploy-with-smp/stream",
            payload=payload,
            gateway_name=gateway_name,
            mac_address=mac_address,
            deployment_type="smp"
        )
            
    def deploy_gateway(self, row: Dict[str, str], deployment_type: str) -> DeploymentResult:
        """Deploy a single gateway based on deployment_type."""
        deployment_type = deployment_type.lower().strip()
        
        if deployment_type == 's1c':
            return self.deploy_smart1_cloud(row)
        elif deployment_type == 'lsm':
            return self.deploy_lsm(row)
        elif deployment_type == 'sms':
            return self.deploy_sms(row)
        elif deployment_type == 'smp':
            return self.deploy_smp(row)
        else:
            gateway_name = row.get('gateway_name', 'Unknown')
            mac_address = row.get('mac_address', '')
            self.log(f"[FAIL] {gateway_name}: Unknown deployment type '{deployment_type}'", "ERROR")
            return DeploymentResult(
                gateway_name=gateway_name,
                mac_address=mac_address,
                deployment_type=deployment_type,
                success=False,
                message="Invalid deployment type",
                error=f"Unknown deployment_type: {deployment_type}. Must be s1c, lsm, sms, or smp"
            )
            
    def deploy_from_csv(self, csv_path: str, deployment_type: str, dry_run: bool = False,
                         filter_gateway: Optional[str] = None, set_mac: Optional[str] = None) -> List[DeploymentResult]:
        """
        Deploy all gateways from a CSV file.
        
        Args:
            csv_path: Path to the CSV file
            deployment_type: Type of deployment (s1c, lsm, sms, smp)
            dry_run: If True, only validate CSV without deploying
            filter_gateway: If specified, only deploy the gateway with this name
            set_mac: If specified (with filter_gateway), replace the MAC address
            
        Returns:
            List of deployment results
        """
        csv_file = Path(csv_path)
        if not csv_file.exists():
            self.log(f"CSV file not found: {csv_path}", "ERROR")
            sys.exit(1)
            
        deployment_type = deployment_type.lower().strip()
        self.log(f"Deployment type: {deployment_type.upper()}")
        self.log(f"Reading CSV file: {csv_path}")
        
        with open(csv_file, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        if not rows:
            self.log("CSV file is empty", "ERROR")
            sys.exit(1)
        
        # Apply filter if specified
        if filter_gateway:
            original_count = len(rows)
            rows = [row for row in rows if row.get('gateway_name') == filter_gateway]
            if not rows:
                self.log(f"No gateway found with name: {filter_gateway}", "ERROR")
                sys.exit(1)
            self.log(f"Filtered to {len(rows)} gateway(s) matching '{filter_gateway}' (from {original_count} total)")
            
            # Apply MAC address override if specified
            if set_mac:
                for row in rows:
                    old_mac = row.get('mac_address', '')
                    row['mac_address'] = set_mac
                    self.log(f"Overriding MAC address: {old_mac} -> {set_mac}")
            
        self.log(f"Found {len(rows)} gateways to deploy")
        
        # Validate required fields based on deployment type
        for i, row in enumerate(rows, 1):
            if deployment_type == 's1c':
                required = ['mac_address', 'account_id', 'template_id', 'template_name', 'gateway_name', 'sic_otp']
                missing = [f for f in required if not row.get(f)]
                if missing:
                    self.log(f"Row {i} (Smart-1 Cloud): Missing required fields: {', '.join(missing)}", "ERROR")
                    sys.exit(1)
                    
            elif deployment_type == 'lsm':
                required = ['mac_address', 'account_id', 'template_name', 'gateway_name', 'sic_otp', 'security_profile', 'provisioning_profile']
                missing = [f for f in required if not row.get(f)]
                if missing:
                    self.log(f"Row {i} (LSM): Missing required fields: {', '.join(missing)}", "ERROR")
                    sys.exit(1)
                    
            elif deployment_type == 'sms':
                required = ['mac_address', 'account_id', 'template_name', 'gateway_name', 'mgmt_server_ip', 'sic_otp', 'gateway_ipv4', 'version', 'hardware']
                missing = [f for f in required if not row.get(f)]
                if missing:
                    self.log(f"Row {i} (SMS): Missing required fields: {', '.join(missing)}", "ERROR")
                    sys.exit(1)

            elif deployment_type == 'smp':
                required = ['mac_address', 'account_id', 'template_id', 'template_name', 'gateway_name']
                missing = [f for f in required if not row.get(f)]
                if missing:
                    self.log(f"Row {i} (SMP): Missing required fields: {', '.join(missing)}", "ERROR")
                    sys.exit(1)
                    
            else:
                self.log(f"Invalid deployment_type: {deployment_type}. Must be s1c, lsm, sms, or smp", "ERROR")
                sys.exit(1)
                
        self.log("CSV validation passed")
        
        if dry_run:
            self.log("Dry run mode - no deployments will be executed")
            for row in rows:
                name = row.get('gateway_name', row.get('mac_address', 'Unknown'))
                self.log(f"  Would deploy: {name} ({row['mac_address']}) via {deployment_type}")
            return []
            
        # Deploy each gateway
        self.log("=" * 60)
        self.log(f"Starting batch deployment ({deployment_type.upper()})")
        self.log("=" * 60)
        
        total_start = time.time()
        
        for i, row in enumerate(rows, 1):
            self.log(f"[{i}/{len(rows)}] Processing gateway...")
            result = self.deploy_gateway(row, deployment_type)
            self.results.append(result)
            self.log("")  # Blank line between deployments
            
        total_duration = time.time() - total_start
        
        # Summary
        self.log("=" * 60)
        self.log("DEPLOYMENT SUMMARY")
        self.log("=" * 60)
        
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        
        self.log(f"Total gateways: {len(self.results)}")
        self.log(f"Successful: {len(successful)}")
        self.log(f"Failed: {len(failed)}")
        self.log(f"Total duration: {total_duration:.1f}s")
        
        if successful:
            self.log("")
            self.log("Successful deployments:")
            for r in successful:
                self.log(f"  [OK] {r.gateway_name} ({r.mac_address}) - {r.deployment_type} - {r.duration_seconds:.1f}s")
                if r.activation_link:
                    self.log(f"      Activation: {r.activation_link}")
                    
        if failed:
            self.log("")
            self.log("Failed deployments:")
            for r in failed:
                self.log(f"  [FAIL] {r.gateway_name} ({r.mac_address}) - {r.deployment_type}")
                self.log(f"      Error: {r.error}")
                
        return self.results
        
    def export_results(self, output_path: str):
        """Export deployment results to a CSV file."""
        if not self.results:
            self.log("No results to export")
            return
            
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['gateway_name', 'mac_address', 'deployment_type', 'success', 
                         'message', 'activation_link', 'duration_seconds', 'error']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for r in self.results:
                writer.writerow({
                    'gateway_name': r.gateway_name,
                    'mac_address': r.mac_address,
                    'deployment_type': r.deployment_type,
                    'success': r.success,
                    'message': r.message,
                    'activation_link': r.activation_link or '',
                    'duration_seconds': f"{r.duration_seconds:.1f}",
                    'error': r.error or ''
                })
                
        self.log(f"Results exported to: {output_path}")


def create_sample_csv(deployment_type: str, output_path: str = None):
    """Create a sample CSV file for a specific deployment type."""
    
    if deployment_type == 's1c':
        if not output_path:
            output_path = 'sample_smart1_cloud.csv'
        sample_data = [
            {
                'mac_address': '00:1C:7F:00:00:01',
                'account_id': '12345678',
                'template_id': '123456789',
                'template_name': 'MyGaiaTemplate',
                'gateway_name': 'gateway-s1c-01',
                'sic_otp': 'vpn123',
                'user_script': '# Optional clish commands',
                'time_zone': 'UTC',
                'hardware': '',
                'gateway_type': 'APPLIANCE_OR_OPENSERVER',
                'identification_method': 'GATEWAY_NAME',
                'os_version': 'R81.10',
                'firewall': 'true',
                'vpn': 'true',
                'ips': 'true',
                'application_control': 'true',
                'url_filtering': 'true',
                'anti_bot': 'true',
                'anti_virus': 'true',
                'threat_emulation': 'true',
                'policy_name': 'Standard',
                'vpn_community': 'MyVPNCommunity',
                'vpn_role': 'satellite'
            },
            {
                'mac_address': '00:1C:7F:00:00:02',
                'account_id': '12345678',
                'template_id': '123456789',
                'template_name': 'MyGaiaTemplate',
                'gateway_name': 'gateway-s1c-02',
                'sic_otp': 'vpn456',
                'user_script': '',
                'time_zone': 'America/New_York',
                'hardware': 'Check Point 6200',
                'gateway_type': 'APPLIANCE_OR_OPENSERVER',
                'identification_method': 'GATEWAY_NAME',
                'os_version': 'R82',
                'firewall': 'true',
                'vpn': 'true',
                'ips': 'false',
                'application_control': 'true',
                'url_filtering': 'false',
                'anti_bot': 'false',
                'anti_virus': 'false',
                'threat_emulation': 'false',
                'policy_name': '',
                'vpn_community': '',
                'vpn_role': ''
            }
        ]
        
    elif deployment_type == 'lsm':
        if not output_path:
            output_path = 'sample_lsm.csv'
        sample_data = [
            # Gaia gateway example
            {
                'mac_address': '00:1C:7F:00:00:01',
                'account_id': '12345678',
                'template_name': 'Master_LSM-3950',
                'gateway_name': 'gateway-lsm-gaia-01',
                'mgmt_server_ip': '192.168.10.78',
                'sic_otp': 'vpn123',
                'security_profile': 'SP_3950-G1',
                'provisioning_profile': 'HWP_3950_G1',
                'domain': ''
            },
            # Spark gateway example
            {
                'mac_address': '00:1C:7F:00:00:02',
                'account_id': '12345678',
                'template_name': 'Master_LSM-Spark1590',
                'gateway_name': 'gateway-lsm-spark-01',
                'mgmt_server_ip': '192.168.10.78',
                'sic_otp': 'vpn456',
                'security_profile': 'SP_1590-Spark',
                'provisioning_profile': 'HWP_1590_Spark',
                'domain': 'Sub_Dom1'
            }
        ]
        
    elif deployment_type == 'sms':
        if not output_path:
            output_path = 'sample_sms.csv'
        sample_data = [
            {
                'mac_address': '00:1C:7F:00:00:01',
                'account_id': '12345678',
                'template_name': 'Master_SMS-Gaia',
                'gateway_name': 'gateway-sms-01',
                'mgmt_server_ip': '192.168.10.78',
                'sic_otp': 'vpn123',
                'gateway_ipv4': '10.0.0.1',
                'version': 'R81.10',
                'hardware': 'Check Point 3200',
                'policy_name': 'Standard',
                'enable_app_control': 'true',
                'enable_ips': 'true',
                'enable_url_filtering': 'false',
                'enable_content_awareness': 'false',
                'enable_ipsec': 'true',
                'vpn_community': 'MyVPNCommunity',
                'vpn_role': 'satellite',
                'domain': ''
            },
            {
                'mac_address': '00:1C:7F:00:00:02',
                'account_id': '12345678',
                'template_name': 'Master_SMS-Gaia',
                'gateway_name': 'gateway-sms-02',
                'mgmt_server_ip': '192.168.10.78',
                'sic_otp': 'vpn456',
                'gateway_ipv4': '10.0.0.2',
                'version': 'R81.10',
                'hardware': 'Check Point 3200',
                'policy_name': 'Standard',
                'enable_app_control': 'true',
                'enable_ips': 'true',
                'enable_url_filtering': 'true',
                'enable_content_awareness': 'true',
                'enable_ipsec': 'true',
                'vpn_community': 'MyVPNCommunity',
                'vpn_role': 'center',
                'domain': 'Sub_Dom1'
            }
        ]

    elif deployment_type == 'smp':
        if not output_path:
            output_path = 'sample_smp.csv'
        sample_data = [
            {
                'mac_address': '00:1C:7F:00:00:01',
                'account_id': '12345678',
                'template_name': 'Master_S1C-Spark',
                'gateway_name': 'gateway-smp-01',
                'mgmt_server_ip': '192.168.10.78',
                'sic_otp': 'vpn123',
                'gateway_ipv4': '10.0.0.1',
                'version': 'R81.10',
                'hardware': 'Check Point 1590',
                'policy_name': 'Standard',
                'enable_app_control': 'true',
                'enable_ips': 'true',
                'enable_url_filtering': 'false',
                'enable_content_awareness': 'false',
                'enable_ipsec': 'true',
                'vpn_community': 'MyVPNCommunity',
                'vpn_role': 'satellite',
                'domain': ''
            },
            {
                'mac_address': '00:1C:7F:00:00:02',
                'account_id': '12345678',
                'template_name': 'Master_S1C-Spark',
                'gateway_name': 'gateway-smp-02',
                'mgmt_server_ip': '192.168.10.78',
                'sic_otp': 'vpn456',
                'gateway_ipv4': '10.0.0.2',
                'version': 'R81.10',
                'hardware': 'Check Point 1590',
                'policy_name': 'Standard',
                'enable_app_control': 'true',
                'enable_ips': 'true',
                'enable_url_filtering': 'false',
                'enable_content_awareness': 'false',
                'enable_ipsec': 'true',
                'vpn_community': 'MyVPNCommunity',
                'vpn_role': 'satellite',
                'domain': ''
            }
        ]
        
    else:
        print(f"Unknown deployment type: {deployment_type}")
        print("Valid types: s1c, lsm, sms, smp")
        sys.exit(1)
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = sample_data[0].keys()
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sample_data)
        
    print(f"Sample CSV for {deployment_type.upper()} created: {output_path}")


def create_all_sample_csvs():
    """Create sample CSV files for all deployment types."""
    for dtype in ['s1c', 'lsm', 'sms', 'smp']:
        create_sample_csv(dtype)


def main():
    parser = argparse.ArgumentParser(
        description="Batch Gateway Deployment Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Deploy Smart-1 Cloud gateways
    python deploy-batch.py --deployment-type s1c --csv smart1_cloud_gateways.csv
    
    # Deploy LSM gateways (Gaia or Spark)
    python deploy-batch.py --deployment-type lsm --csv lsm_gateways.csv --api-url http://localhost:8000
    
    # Deploy SMS gateways
    python deploy-batch.py --deployment-type sms --csv sms_gateways.csv
    
    # Deploy SMP gateways (Spark only)
    python deploy-batch.py --deployment-type smp --csv smp_gateways.csv
    
    # Dry run (validate only)
    python deploy-batch.py --deployment-type lsm --csv lsm_gateways.csv --dry-run
    
    # Create sample CSV for a specific deployment type
    python deploy-batch.py --create-sample s1c
    python deploy-batch.py --create-sample lsm
    python deploy-batch.py --create-sample sms
    python deploy-batch.py --create-sample smp
    
    # Create all sample CSVs
    python deploy-batch.py --create-all-samples
    
    # Export results
    python deploy-batch.py --deployment-type sms --csv sms_gateways.csv --output results.csv
    
    # Verbose mode
    python deploy-batch.py --deployment-type lsm --csv lsm_gateways.csv -v
    
    # Deploy only a specific gateway by name
    python deploy-batch.py --deployment-type sms --csv sms_gateways.csv --filter gateway-01
    
    # Deploy specific gateway with overridden MAC address
    python deploy-batch.py --deployment-type sms --csv sms_gateways.csv --filter gateway-01 --set-mac 00:1C:7F:AA:BB:CC
"""
    )
    
    parser.add_argument('--deployment-type', '-t',
                       choices=['s1c', 'lsm', 'sms', 'smp'],
                       help='Deployment type: s1c, lsm, sms, or smp')
    parser.add_argument('--csv', help='Path to CSV file with gateway data')
    parser.add_argument('--api-url', default='http://localhost:8000', 
                       help='Gateway Deployer API URL (default: http://localhost:8000)')
    parser.add_argument('--create-sample', metavar='TYPE',
                       choices=['s1c', 'lsm', 'sms', 'smp'],
                       help='Create a sample CSV file for specified deployment type')
    parser.add_argument('--create-all-samples', action='store_true',
                       help='Create sample CSV files for all deployment types')
    parser.add_argument('--sample-output', metavar='FILE',
                       help='Output path for sample CSV (used with --create-sample)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Validate CSV without deploying')
    parser.add_argument('--output', '-o', metavar='FILE',
                       help='Export results to CSV file')
    parser.add_argument('--timeout', type=int, default=600,
                       help='Request timeout in seconds (default: 600)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--filter', metavar='GATEWAY_NAME',
                       help='Filter to deploy only the specified gateway name')
    parser.add_argument('--set-mac', metavar='MAC_ADDR',
                       help='Replace MAC address for the filtered gateway (requires --filter)')
    
    args = parser.parse_args()
    
    # Validate --set-mac requires --filter
    if args.set_mac and not args.filter:
        parser.error("--set-mac requires --filter to be specified")
    
    # Create all sample CSVs
    if args.create_all_samples:
        create_all_sample_csvs()
        return
    
    # Create sample CSV for specific type
    if args.create_sample:
        create_sample_csv(args.create_sample, args.sample_output)
        return
        
    # Validate arguments for deployment
    if not args.csv:
        parser.error("--csv is required (or use --create-sample TYPE or --create-all-samples)")
        
    if not args.deployment_type:
        parser.error("--deployment-type is required (s1c, lsm, sms, or smp)")
        
    # Run deployment
    with BatchDeployer(args.api_url, timeout=args.timeout, verbose=args.verbose) as deployer:
        results = deployer.deploy_from_csv(
            args.csv, 
            args.deployment_type, 
            dry_run=args.dry_run,
            filter_gateway=args.filter,
            set_mac=args.set_mac
        )
        
        if args.output and results:
            deployer.export_results(args.output)
            
        # Exit with error code if any failed
        if results and any(not r.success for r in results):
            sys.exit(1)


if __name__ == '__main__':
    main()
