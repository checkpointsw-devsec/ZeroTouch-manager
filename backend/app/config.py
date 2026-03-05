"""
Configuration management for the Check Point Gateway Deployer application.
"""
from pydantic_settings import BaseSettings
from typing import List
import os
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application Settings
    app_name: str = "Check Point Gateway Deployer"
    app_version: str = "1.0.0"
    debug: bool = True
    secret_key: str = "change-this-secret-key-in-production"

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000

    # Zero Touch Portal API
    zero_touch_base_url: str = "https://cloudinfra-gw.portal.checkpoint.com"
    zero_touch_client_id: str = ""
    zero_touch_secret_key: str = ""

    # Smart-1 Cloud Management API
    # URL format: https://{tenant}.maas.checkpoint.com (e.g., https://mytenant.maas.checkpoint.com)
    smart1_cloud_base_url: str = ""
    smart1_cloud_api_key: str = ""  # API key for Management API authentication
    smart1_cloud_secret_key: str = ""  # Fallback: used if api_key is empty

    # On-Premises Management Server (LSM)
    mgmt_base_url: str = ""
    mgmt_server_host: str = ""
    mgmt_server_port: int = 443
    mgmt_server_api_key: str = ""
    mgmt_server_username: str = ""
    mgmt_server_password: str = ""

    # Session Settings
    session_timeout: int = 3600
    session_secret: str = "change-this-session-secret-in-production"
    
    # CORS Settings
    allowed_origins: List[str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ]
    
    # API request/response body logging
    # Values: "none" (default) | "req" | "resp" | "all"
    api_debug: str = "none"

    # SIC polling timeout (seconds)
    # How long to wait for SIC trust to reach "communicating" during deployment.
    sic_timeout: int = 900  # default 15 minutes

    # TLS verification for management server connections
    # Set to True in production with proper certificates
    ssl_verify: bool = False

    # Logging
    log_level: str = "INFO"   # Maps to LOG_LEVEL in .env
    log_file: str = "logs/app.log"
    show_secrets_in_logfile: bool = False  # When True, passwords/OTPs are logged in plain text
    
    class Config:
        # Find .env file relative to this config file
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the application settings instance."""
    return settings


def _redact_payload(obj, _sensitive_keys=frozenset({
    "api-key", "api_key", "apikey",
    "one-time-password", "sic-otp", "sic_otp",
    "new-sic-key", "sic-key", "sic_key",
    "password", "secret", "secret_key", "secret-key",
    "access_token", "token", "sid",
})):
    """Return a deep copy of *obj* with sensitive values replaced by '***'."""
    if settings.show_secrets_in_logfile:
        return obj
    if isinstance(obj, dict):
        return {
            k: ("***" if k.lower() in _sensitive_keys else _redact_payload(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_redact_payload(item) for item in obj]
    return obj


def log_http_request(logger_fn, label: str, payload) -> None:
    """Log an outgoing HTTP request body if API_DEBUG includes requests."""
    mode = settings.api_debug.lower()
    if mode in ("req", "all"):
        import json
        try:
            formatted = json.dumps(_redact_payload(payload), indent=2, default=str)
        except Exception:
            formatted = str(payload)
        logger_fn(f"{label} request body:\n{formatted}")


def log_http_response(logger_fn, label: str, status_code, body) -> None:
    """Log an incoming HTTP response body if API_DEBUG includes responses."""
    mode = settings.api_debug.lower()
    if mode in ("resp", "all"):
        import json
        # If body is a raw string, try to parse → redact → re-serialize
        if isinstance(body, str):
            try:
                body = json.dumps(_redact_payload(json.loads(body)), indent=2, default=str)
            except (json.JSONDecodeError, TypeError):
                pass  # leave as-is
        elif isinstance(body, (dict, list)):
            body = json.dumps(_redact_payload(body), indent=2, default=str)
        logger_fn(f"{label} response [{status_code}]:\n{body}")
