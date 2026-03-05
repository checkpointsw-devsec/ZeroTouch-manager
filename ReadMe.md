# Check Point Gateway Deployer — Lab Guide

A hands-on lab guide for deploying Check Point gateways using the **Check Point Gateway Deployer** web application and batch CLI tool. This guide walks through installation, configuration, and usage of every deployment flow.

> **Note:** Screenshots will be added to this guide in a future revision. Placeholder markers `[screenshot]` indicate where they should be inserted.

---

## Table of Contents

1. [Lab Overview](#1-lab-overview)
2. [Lab Prerequisites](#2-lab-prerequisites)
3. [Installation and Setup](#3-installation-and-setup)
   - 3.1 [Clone and Install](#31-clone-and-install)
   - 3.2 [Directory Structure](#32-directory-structure)
4. [Configuring the .env File](#4-configuring-the-env-file)
   - 4.1 [Minimal .env for Smart-1 Cloud](#41-minimal-env-for-smart-1-cloud)
   - 4.2 [Minimal .env for SMS / LSM](#42-minimal-env-for-sms--lsm)
   - 4.3 [Minimal .env for SMP](#43-minimal-env-for-smp)
   - 4.4 [Full .env Reference](#44-full-env-reference)
   - 4.5 [Common .env Mistakes](#45-common-env-mistakes)
5. [Starting the Server](#5-starting-the-server)
6. [Nginx Reverse Proxy and HTTPS](#6-nginx-reverse-proxy-and-https)
   - 6.1 [Why Use Nginx](#61-why-use-nginx)
   - 6.2 [Systemd Service for the Backend](#62-systemd-service-for-the-backend)
   - 6.3 [HTTP-Only Configuration (Lab)](#63-http-only-configuration-lab)
   - 6.4 [HTTPS with Self-Signed Certificate](#64-https-with-self-signed-certificate)
   - 6.5 [HTTPS with Let's Encrypt](#65-https-with-lets-encrypt)
   - 6.6 [Nginx on Windows](#66-nginx-on-windows)
   - 6.7 [Updating ALLOWED_ORIGINS after Nginx](#67-updating-allowed_origins-after-nginx)
   - 6.8 [Firewall Rules](#68-firewall-rules)
7. [Preparing Zero Touch Templates](#7-preparing-zero-touch-templates)
   - 7.1 [Spark Templates](#71-spark-templates)
   - 7.2 [Gaia Templates](#72-gaia-templates)
   - 7.3 [User-Script Placeholders](#73-user-script-placeholders)
   - 7.4 [File Injection (##!!)](#74-file-injection-)
8. [Preparing CSV Files for Batch Deployment](#8-preparing-csv-files-for-batch-deployment)
   - 8.1 [Smart-1 Cloud CSV](#81-smart-1-cloud-csv)
   - 8.2 [SMS CSV](#82-sms-csv)
   - 8.3 [LSM CSV](#83-lsm-csv)
   - 8.4 [SMP CSV](#84-smp-csv)
   - 8.5 [CSV Tips and Validation](#85-csv-tips-and-validation)
9. [Web Application Walkthrough](#9-web-application-walkthrough)
   - 9.1 [Opening the Application](#91-opening-the-application)
   - 9.2 [Step 1 — Login to Zero Touch Portal](#92-step-1--login-to-zero-touch-portal)
   - 9.3 [Step 2 — Select Account](#93-step-2--select-account)
   - 9.4 [Step 3 — Choose Gateway Type](#94-step-3--choose-gateway-type)
   - 9.5 [Step 4 — Select Template](#95-step-4--select-template)
   - 9.6 [Step 5 — Enter MAC Address and Gateway Name](#96-step-5--enter-mac-address-and-gateway-name)
   - 9.7 [Step 6 — Select Management Platform](#97-step-6--select-management-platform)
   - 9.8 [Step 7 — Claim the Gateway](#98-step-7--claim-the-gateway)
   - 9.9 [Step 8 — Configure Deployment Parameters](#99-step-8--configure-deployment-parameters)
   - 9.10 [Step 9 — Review the User Script](#910-step-9--review-the-user-script)
   - 9.11 [Step 10 — Network Configuration (Gaia Only)](#911-step-10--network-configuration-gaia-only)
   - 9.12 [Step 11 — Deploy](#912-step-11--deploy)
   - 9.13 [Step 12 — Monitor the Deployment Log](#913-step-12--monitor-the-deployment-log)
   - 9.14 [Unclaiming a Gateway](#914-unclaiming-a-gateway)
   - 9.15 [Resetting the Workflow](#915-resetting-the-workflow)
10. [Batch Deployment with deploy-batch.py](#10-batch-deployment-with-deploy-batchpy)
    - 10.1 [Basic Usage](#101-basic-usage)
    - 10.2 [Filtering a Single Gateway](#102-filtering-a-single-gateway)
    - 10.3 [Replacing a Gateway (MAC Override)](#103-replacing-a-gateway-mac-override)
    - 10.4 [Dry Run Validation](#104-dry-run-validation)
    - 10.5 [Exporting Results to CSV](#105-exporting-results-to-csv)
    - 10.6 [Remote Backend](#106-remote-backend)
    - 10.7 [Creating Sample CSV Templates](#107-creating-sample-csv-templates)
11. [Lab Exercises](#11-lab-exercises)
    - Exercise 1 — Deploy a Spark Gateway to Smart-1 Cloud
    - Exercise 2 — Deploy a Gaia Gateway to SMS
    - Exercise 3 — Batch Deploy Multiple Gateways to LSM
    - Exercise 4 — SMP Zero Touch Only Deployment
12. [Logging and Debugging](#12-logging-and-debugging)
13. [API Reference (Quick Summary)](#13-api-reference-quick-summary)
14. [Troubleshooting](#14-troubleshooting)
15. [Security Considerations](#15-security-considerations)
16. [Appendix — Config File Injection Examples](#16-appendix--config-file-injection-examples)

---

## 1. Lab Overview

The **Check Point Gateway Deployer** automates the provisioning and deployment of Check Point gateways through the **Zero Touch Portal**. It supports four management platform flows:

| Flow | Management Platform | Gateway Types | Key Actions |
|---|---|---|---|
| **Smart-1 Cloud** | Check Point Smart-1 Cloud (MaaS) | Spark + Gaia | Creates gateway in cloud, injects MaaS token, configures blades + VPN |
| **SMS** | On-premises SMS or MDS | Spark + Gaia | Creates gateway object, establishes SIC, configures blades + policy |
| **LSM** | On-premises LSM (Large Scale Management) | Gaia | Adds LSM gateway with security/provisioning profiles |
| **SMP** | Spark Management Portal | Spark only | Zero Touch claim + release — no management server interaction |

**Architecture:**

```
Browser (Vue.js)  ──or──  deploy-batch.py (CLI)
         │                        │
         │  HTTP / SSE            │  HTTP / SSE
         ▼                        ▼
   FastAPI Backend (Python)
         │
    ┌────┼────┬────────┐
    ▼    ▼    ▼        ▼
  S1C  SMS  LSM      SMP
  Orch Orch Orch     Orch
    │    │    │        │
    ▼    ▼    ▼        ▼
 Smart-1  SMS/MDS   Zero
 Cloud              Touch
    │    │    │     Portal
    └────┴────┴────────┘
         ▼
   Zero Touch Portal
```

---

## 2. Lab Prerequisites

Before starting, ensure you have the following:

| Requirement | Details |
|---|---|
| **Python 3.11+** | Installed on the deployer host |
| **Zero Touch Portal account** | Client ID + Secret Key (Account Settings in the Zero Touch Portal) |
| **Zero Touch templates** | Pre-configured for each gateway hardware model (Spark and/or Gaia) |
| **Smart-1 Cloud tenant** *(for S1C flow)* | Tenant URL + API Key or Secret Key |
| **SMS / MDS** *(for SMS/LSM flows)* | On-premises management server reachable from the deployer host, with an API key configured |
| **Gateway hardware** | Physical gateway appliances with known MAC addresses |
| **Network connectivity** | Deployer host can reach Zero Touch Portal (HTTPS) and the management server (port 443) |

---

## 3. Installation and Setup

### 3.1 Clone and Install

```bash
# Clone the repository
git clone <repo-url>
cd checkpoint-gateway-deployer

# Create a Python virtual environment
cd backend
python -m venv venv

# Activate the virtual environment
# Windows:
venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

[screenshot: terminal showing successful pip install]

### 3.2 Directory Structure

```
checkpoint-gateway-deployer/
├── deploy-batch.py              # CLI batch deployment tool
├── requirements.txt             # Python dependencies
├── sample_smart1_cloud.csv      # Sample CSV — Smart-1 Cloud
├── sample_sms.csv               # Sample CSV — SMS
├── sample_lsm.csv               # Sample CSV — LSM
├── sample_smp.csv               # Sample CSV — SMP
├── backend/
│   ├── .env                     # Environment configuration (you create this)
│   ├── app/
│   │   ├── main.py              # FastAPI application entry point
│   │   ├── config.py            # Settings loaded from .env
│   │   ├── api/                 # REST API endpoints
│   │   ├── models/              # Pydantic data models
│   │   └── services/            # Orchestrators and service clients
│   ├── config_files/            # Per-gateway configuration files for ##!! injection
│   │   ├── gw-1590-01/
│   │   ├── gw-3950-01/
│   │   └── ...
│   └── logs/                    # Application logs (auto-created)
└── frontend/
    ├── index.html               # Single-page Vue.js application
    ├── js/app.js                # Application logic
    └── css/                     # Stylesheets
```

---

## 4. Configuring the .env File

The `.env` file is the central configuration file for the application. It lives in the `backend/` directory.

**Create it from the sample:**

```bash
cp backend/.sample-env backend/.env
```

Then edit `backend/.env` with your values. Below are flow-specific minimal configurations.

### 4.1 Minimal .env for Smart-1 Cloud

If you only need the Smart-1 Cloud flow:

```ini
# Server
HOST=0.0.0.0
PORT=8000

# Zero Touch Portal (required for all flows)
ZERO_TOUCH_CLIENT_ID=your-zt-client-id
ZERO_TOUCH_SECRET_KEY=your-zt-secret-key

# Smart-1 Cloud
SMART1_CLOUD_BASE_URL=https://your-tenant.maas.checkpoint.com/your-tenant-id/web_api
SMART1_CLOUD_SECRET_KEY=your-s1c-secret-key

# Logging
LOG_LEVEL=INFO
```

### 4.2 Minimal .env for SMS / LSM

If you need the SMS or LSM flows (on-premises management server):

```ini
# Server
HOST=0.0.0.0
PORT=8000

# Zero Touch Portal
ZERO_TOUCH_CLIENT_ID=your-zt-client-id
ZERO_TOUCH_SECRET_KEY=your-zt-secret-key

# Management Server
MGMT_BASE_URL=https://192.168.10.78:443/web_api/
MGMT_SERVER_PORT=443
MGMT_SERVER_API_KEY=your-mgmt-api-key

# Logging
LOG_LEVEL=INFO
```

### 4.3 Minimal .env for SMP

SMP only needs Zero Touch credentials — no management server:

```ini
HOST=0.0.0.0
PORT=8000

ZERO_TOUCH_CLIENT_ID=your-zt-client-id
ZERO_TOUCH_SECRET_KEY=your-zt-secret-key
```

### 4.4 Full .env Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `HOST` | | `0.0.0.0` | Bind address. Use `127.0.0.1` behind Nginx. |
| `PORT` | | `8000` | Listen port |
| `ZERO_TOUCH_CLIENT_ID` | **Yes** | | OAuth client ID from Zero Touch Portal |
| `ZERO_TOUCH_SECRET_KEY` | **Yes** | | OAuth secret key from Zero Touch Portal |
| `SMART1_CLOUD_BASE_URL` | S1C only | | Tenant-specific MaaS URL |
| `SMART1_CLOUD_SECRET_KEY` | S1C only | | Smart-1 Cloud API secret key |
| `SMART1_CLOUD_API_KEY` | S1C only | | Alternative: Management API key (used if secret key is empty) |
| `MGMT_BASE_URL` | SMS/LSM | | Full URL to management server Web API |
| `MGMT_SERVER_PORT` | | `443` | Management API port |
| `MGMT_SERVER_API_KEY` | SMS/LSM | | API key with R/W permissions on the management server |
| `ALLOWED_ORIGINS` | | `["http://localhost:8000"]` | CORS origins — update for production/Nginx |
| `LOG_LEVEL` | | `INFO` | `DEBUG`, `INFO`, `WARNING` |
| `API_DEBUG` | | `none` | HTTP body logging: `none`, `req`, `resp`, `all` |
| `DEBUG` | | `false` | Enable console (stderr) logging |
| `SIC_TIMEOUT` | | `900` | Seconds to wait for SIC trust establishment |
| `SSL_VERIFY` | | `false` | TLS certificate verification for management server |
| `SESSION_TIMEOUT` | | `3600` | Session timeout in seconds |
| `SECRET_KEY` | | (default) | Application secret — change in production |
| `SESSION_SECRET` | | (default) | Session signing secret — change in production |
| `SHOW_SECRETS_IN_LOGFILE` | | `false` | Log passwords/OTPs in plain text (debugging only) |

### 4.5 Common .env Mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Missing `ZERO_TOUCH_CLIENT_ID` | Login fails immediately | Add your Zero Touch credentials |
| Wrong `MGMT_BASE_URL` format | SMS/LSM deployment fails at login | Use full URL: `https://<ip>:443/web_api/` |
| `ALLOWED_ORIGINS` not updated after Nginx | CORS errors in browser console | Set to your Nginx URL, e.g. `["https://192.168.1.100"]` |
| Spaces around `=` in .env | Variables not parsed correctly | Use `KEY=value` with no spaces |
| Quotes inside JSON arrays | Parse error | Use `ALLOWED_ORIGINS=["https://host"]` — double quotes inside, no outer single quotes |

---

## 5. Starting the Server

```bash
cd backend

# Ensure virtual environment is activated
# Windows: venv\Scripts\activate
# Linux:   source venv/bin/activate

python -m app.main
```

You should see output similar to:

```
INFO     Check Point Gateway Deployer v1.0.0
INFO     Uvicorn running on http://0.0.0.0:8000
```

**Verify the server is running:**

- **Web UI:** Open http://localhost:8000 in your browser
- **API Docs:** Open http://localhost:8000/docs for interactive Swagger documentation
- **Health Check:** `curl http://localhost:8000/health`

[screenshot: browser showing the main page with empty sidebar]

---

## 6. Nginx Reverse Proxy and HTTPS

### 6.1 Why Use Nginx

The FastAPI backend runs on plain HTTP. For any deployment beyond a single laptop you should put Nginx in front to:

- **Terminate HTTPS** — credentials and SIC passwords are not sent in clear text
- **Standard ports** — serve on 80/443 instead of 8000
- **Buffering** — protect the Python server from slow client connections
- **Security headers** — HSTS, CSP, etc.

### 6.2 Systemd Service for the Backend

Create a systemd unit so the backend starts automatically and restarts on failure:

```bash
sudo nano /etc/systemd/system/gw-deployer.service
```

```ini
[Unit]
Description=Check Point Gateway Deployer
After=network.target

[Service]
Type=simple
User=deployuser
WorkingDirectory=/opt/checkpoint-gateway-deployer/backend
ExecStart=/opt/checkpoint-gateway-deployer/backend/venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
EnvironmentFile=/opt/checkpoint-gateway-deployer/backend/.env

[Install]
WantedBy=multi-user.target
```

> **Adjust paths and username** to match your actual installation.

```bash
sudo systemctl daemon-reload
sudo systemctl enable gw-deployer
sudo systemctl start gw-deployer

# Verify it is running
sudo systemctl status gw-deployer
```

Update `.env` to bind to localhost only (Nginx handles external traffic):

```ini
HOST=127.0.0.1
PORT=8000
```

### 6.3 HTTP-Only Configuration (Lab)

For internal lab networks where HTTPS is not required:

**Install Nginx:**

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y nginx

# RHEL / Rocky
sudo dnf install -y nginx
```

**Create the site configuration:**

```bash
sudo nano /etc/nginx/sites-available/gw-deployer
```

```nginx
server {
    listen 80;
    server_name _;

    proxy_read_timeout      900s;
    proxy_connect_timeout   10s;
    proxy_send_timeout      900s;

    location / {
        proxy_pass          http://127.0.0.1:8000;
        proxy_http_version  1.1;

        # Required for SSE (Server-Sent Events) streaming
        proxy_set_header    Connection          "";
        proxy_buffering     off;
        proxy_cache         off;
        chunked_transfer_encoding on;

        proxy_set_header    Host               $host;
        proxy_set_header    X-Real-IP          $remote_addr;
        proxy_set_header    X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header    X-Forwarded-Proto  $scheme;
    }
}
```

**Enable and start:**

```bash
sudo ln -s /etc/nginx/sites-available/gw-deployer /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

> **Important:** The `proxy_read_timeout 900s` is critical — deployments use SSE streaming and can run for up to 15 minutes while waiting for SIC establishment.

### 6.4 HTTPS with Self-Signed Certificate

**Generate a SAN certificate:**

```bash
sudo mkdir -p /etc/nginx/ssl

cat > /tmp/san.conf << 'EOF'
[req]
default_bits       = 4096
prompt             = no
default_md         = sha256
distinguished_name = dn
x509_extensions    = v3_req

[dn]
C  = US
ST = State
L  = City
O  = Organization
CN = gw-deployer

[v3_req]
subjectAltName = @alt_names

[alt_names]
IP.1  = 192.168.1.100       # Replace with your server IP
DNS.1 = gw-deployer.local   # Replace with your hostname
DNS.2 = localhost
EOF

sudo openssl req -x509 -nodes -days 3650 -newkey rsa:4096 \
    -keyout /etc/nginx/ssl/gw-deployer.key \
    -out    /etc/nginx/ssl/gw-deployer.crt \
    -config /tmp/san.conf

sudo chmod 600 /etc/nginx/ssl/gw-deployer.key
```

**Nginx HTTPS configuration:**

```nginx
# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/ssl/gw-deployer.crt;
    ssl_certificate_key /etc/nginx/ssl/gw-deployer.key;

    ssl_protocols             TLSv1.2 TLSv1.3;
    ssl_ciphers               ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache         shared:SSL:10m;

    proxy_read_timeout      900s;
    proxy_connect_timeout   10s;
    proxy_send_timeout      900s;

    location / {
        proxy_pass          http://127.0.0.1:8000;
        proxy_http_version  1.1;
        proxy_set_header    Connection          "";
        proxy_buffering     off;
        proxy_cache         off;
        chunked_transfer_encoding on;
        proxy_set_header    Host               $host;
        proxy_set_header    X-Real-IP          $remote_addr;
        proxy_set_header    X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header    X-Forwarded-Proto  $scheme;
    }
}
```

### 6.5 HTTPS with Let's Encrypt

For internet-facing deployments with a public domain:

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain certificate (replace domain and email)
sudo certbot --nginx -d deployer.example.com --email admin@example.com --agree-tos --non-interactive

# Test automatic renewal
sudo certbot renew --dry-run
```

### 6.6 Nginx on Windows

For development or lab use on Windows:

1. Download Nginx from [nginx.org](https://nginx.org/en/download.html) (Stable version)
2. Extract to `C:\nginx\`
3. Edit `C:\nginx\conf\nginx.conf`

For HTTPS on Windows, use [mkcert](https://github.com/FiloSottile/mkcert) to create locally-trusted certificates:

```cmd
mkcert -install
mkcert localhost 127.0.0.1
```

Run Nginx:

```cmd
cd C:\nginx
nginx.exe
nginx.exe -t       :: Test config
nginx.exe -s reload :: Reload after changes
nginx.exe -s stop   :: Stop
```

### 6.7 Updating ALLOWED_ORIGINS after Nginx

After setting up Nginx, update `backend/.env` to match your public URL:

```ini
# For HTTPS with IP:
ALLOWED_ORIGINS=["https://192.168.1.100"]

# For HTTPS with domain:
ALLOWED_ORIGINS=["https://deployer.example.com"]

# For HTTP lab:
ALLOWED_ORIGINS=["http://192.168.1.100"]
```

Then restart the backend:

```bash
sudo systemctl restart gw-deployer
```

### 6.8 Firewall Rules

```bash
# UFW (Ubuntu/Debian)
sudo ufw allow 'Nginx Full'
sudo ufw deny 8000
sudo ufw enable

# Firewalld (RHEL/Rocky)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

---

## 7. Preparing Zero Touch Templates

Before using the deployer, you need Zero Touch templates configured in the Zero Touch Portal. Each template contains a **user-script** — a clish script that runs on the gateway during First Time Wizard (FTW).

### 7.1 Spark Templates

Spark templates (for 1500 series — 1570, 1590, etc.) must include:

```clish
set hostname <gateway-name>
set sic_init password <sic-key>
```

For Smart-1 Cloud Spark, also include:

```clish
set cloudinfra token <token>
```

### 7.2 Gaia Templates

Gaia templates (for 3000–6000 series — 3950, 3970, etc.) must include:

```clish
set hostname <gateway-name>
```

For SMS/LSM Gaia, include management connectivity:

```clish
# LSM example
LSMenabler -r on -y
cp_conf sic cert_pull <mgmt-server-ip> <gateway-name>
cp_conf intfs set external Mgmt
fw fetch -i <mgmt-server-ip>
fw fetch -i <mgmt-server-ip>
```

### 7.3 User-Script Placeholders

The deployer automatically replaces these placeholders before pushing the configuration:

| Placeholder | Replaced With | Applicable Flows |
|---|---|---|
| `<gateway-name>` | Gateway hostname from the form/CSV | All |
| `<mgmt-server-ip>` | Management server IP | SMS, LSM |
| `<sic-key>` | SIC one-time password | SMS (Spark), LSM, S1C (Spark) |
| `<token>` | MaaS token from Smart-1 Cloud | Smart-1 Cloud |

### 7.4 File Injection (##!!)

Lines in the user-script starting with `##!!` followed by a filename are replaced with the contents of that file at deployment time. This allows per-gateway or shared configuration snippets to be injected.

**Template user-script example:**

```clish
set hostname <gateway-name>
##!! routing-table.conf
##!! firewall-rules.conf
set sic_init password <sic-key>
```

**File lookup order:**

1. `backend/config_files/<gateway-name>/<filename>` — gateway-specific override
2. `backend/config_files/<filename>` — shared fallback

**Example directory structure:**

```
backend/config_files/
├── gw-3950-01/
│   ├── file1.conf     # Specific to gw-3950-01
│   ├── file2.conf
│   └── file3.conf
├── gw-3950-02/
│   ├── file1.conf     # Specific to gw-3950-02
│   └── ...
└── shared-routes.conf # Shared across all gateways
```

If a file is not found, the line is replaced with: `#! file not found: <filename>`

---

## 8. Preparing CSV Files for Batch Deployment

CSV files drive both the batch CLI tool (`deploy-batch.py`) and serve as a reference for web-based deployments. All CSV files use comma separation with a header row. Boolean fields accept `true`/`false` (case-insensitive).

### 8.1 Smart-1 Cloud CSV

**Sample file:** `sample_smart1_cloud.csv`

| Column | Required | Description |
|---|---|---|
| `mac_address` | Yes | Gateway MAC (e.g., `00:1C:7F:9B:AE:8D`) |
| `account_id` | Yes | Zero Touch account ID |
| `template_id` | Yes | Zero Touch template ID |
| `template_name` | Yes | Template name (must match portal exactly) |
| `gateway_name` | Yes | Gateway hostname |
| `sic_otp` | Yes | SIC one-time password (min 4 chars) |
| `user_script` | | Additional clish commands |
| `time_zone` | | Timezone string |
| `hardware` | | Hardware model (e.g., `1570/1590 Appliances`, `3900 Appliances`) |
| `gateway_type` | | `Spark` or `nonSpark` |
| `os_version` | | `R81.10` or `R82.10` |
| `firewall` | | `true`/`false` |
| `vpn` | | `true`/`false` |
| `ips` | | `true`/`false` |
| `application_control` | | `true`/`false` |
| `url_filtering` | | `true`/`false` |
| `anti_bot` | | `true`/`false` |
| `anti_virus` | | `true`/`false` |
| `threat_emulation` | | `true`/`false` |
| `policy_name` | | Policy package to install |
| `vpn_community` | | VPN community name |
| `vpn_role` | | `satellite` or `center` |
| `ipv4_address` | | Gateway IPv4 — sets management IP for Gaia |

**Example row:**

```csv
mac_address,account_id,template_id,template_name,gateway_name,sic_otp,hardware,gateway_type,os_version,policy_name,vpn_community,vpn_role,ipv4_address
00:1C:7F:9B:AE:8D,9075116,570444285,Master_S1C-Spark,gw-1590w-01,vpn123,1570/1590 Appliances,Spark,R81.10,Standard,MucLabLegacy,satellite,192.168.10.130
```

### 8.2 SMS CSV

**Sample file:** `sample_sms.csv`

| Column | Required | Description |
|---|---|---|
| `mac_address` | Yes | Gateway MAC address |
| `account_id` | Yes | Zero Touch account ID |
| `template_id` | | Template ID — required if gateway is not yet claimed |
| `template_name` | Yes | Template name |
| `gateway_name` | Yes | Gateway hostname |
| `mgmt_server_ip` | Yes | Management server IP |
| `sic_otp` | Yes | SIC OTP (min 4 chars) |
| `gateway_ipv4` | Yes | Gateway IPv4 address |
| `version` | Yes | Software version (e.g., `R81.10`, `R82.10`) |
| `hardware` | Yes | Hardware model string |
| `policy_name` | | Policy package |
| `enable_app_control` | | `true`/`false` |
| `enable_ips` | | `true`/`false` |
| `enable_url_filtering` | | `true`/`false` |
| `enable_content_awareness` | | `true`/`false` (**Not supported on 1500 series**) |
| `enable_ipsec` | | `true`/`false` |
| `vpn_community` | | VPN community name |
| `vpn_role` | | `satellite` or `center` |
| `domain` | | Domain name for MDS deployments |

**Example row:**

```csv
mac_address,account_id,template_id,template_name,gateway_name,mgmt_server_ip,sic_otp,gateway_ipv4,version,hardware,policy_name,enable_ipsec,vpn_community,vpn_role,domain
00:1C:7F:CC:E0:8A,9075116,754093852,Master_MDS-3950,gw-3970-01,192.168.10.78,vpnChangeMe,192.168.10.86,R82.10,3900 Appliances,Standard,false,MucLabTest,satellite,Sub_Dom1
```

### 8.3 LSM CSV

**Sample file:** `sample_lsm.csv`

| Column | Required | Description |
|---|---|---|
| `mac_address` | Yes | Gateway MAC address |
| `account_id` | Yes | Zero Touch account ID |
| `template_id` | | Template ID — **claim fails hard if missing and gateway is not claimed** |
| `template_name` | Yes | Template name |
| `gateway_name` | Yes | Gateway hostname |
| `mgmt_server_ip` | | Overrides `MGMT_SERVER_HOST` in `.env` |
| `sic_otp` | Yes | SIC OTP — must match what the device uses in `cp_conf sic cert_pull` |
| `gateway_ipv4` | | Gateway IPv4 — sets `mgmt-eth-ip-address-ipv4` in Zero Touch |
| `security_profile` | Yes | LSM security profile name (e.g., `SP_3950-G1`) |
| `provisioning_profile` | Yes | LSM provisioning profile name (e.g., `HWP_3950_G1`) |
| `domain` | | Domain name for MDS |

**Example row:**

```csv
mac_address,account_id,template_id,template_name,gateway_name,mgmt_server_ip,sic_otp,gateway_ipv4,security_profile,provisioning_profile,domain
00:1C:7F:CC:E0:8A,9075116,581172753,Master_LSM-3950,gw-3970-01,192.168.10.78,vpn202,192.168.10.86,SP_3950-G1,HWP_3950_G1,Sub_Dom1
```

> **Important:** LSM security profiles and provisioning profiles must already exist on the management server before deployment.

### 8.4 SMP CSV

**Sample file:** `sample_smp.csv`

| Column | Required | Description |
|---|---|---|
| `mac_address` | Yes | Gateway MAC address |
| `account_id` | Yes | Zero Touch account ID |
| `template_id` | Yes | Template ID |
| `template_name` | Yes | Template name |
| `gateway_name` | Yes | Gateway hostname |
| `hw_type` | | `Spark` (informational only — not sent to API) |

**Example row:**

```csv
mac_address,account_id,template_id,template_name,gateway_name,hw_type
00:1C:7F:9B:AE:8D,9075116,380053285,Master_SMP-Spark,gw-1590w-01,Spark
```

### 8.5 CSV Tips and Validation

- **No trailing commas** — ensure each row has exactly the same number of fields as the header
- **MAC format** — use colon-separated uppercase hex: `00:1C:7F:XX:XX:XX`
- **Boolean values** — `true` or `false` (case-insensitive)
- **Empty optional fields** — leave the field empty but keep the comma: `,...,`
- **Validate before deploying** — use `--dry-run` with the batch tool:
  ```bash
  python deploy-batch.py -t sms --csv sample_sms.csv --dry-run
  ```
- **Generate sample CSVs** — use the built-in sample generator:
  ```bash
  python deploy-batch.py --create-sample sms
  python deploy-batch.py --create-all-samples
  ```

---

## 9. Web Application Walkthrough

The web application provides a guided, step-by-step workflow for deploying a single gateway at a time.

### 9.1 Opening the Application

Navigate to **http://localhost:8000** (or your Nginx URL) in a modern web browser.

You will see the main layout:
- **Left sidebar** — Setup panel with sequential configuration steps
- **Main area** — Shows a checklist of remaining steps until all sidebar fields are complete

[screenshot: initial application view with empty sidebar and checklist]

### 9.2 Step 1 — Login to Zero Touch Portal

Click the **"Login to Zero Touch"** button in the sidebar.

The application uses the `ZERO_TOUCH_CLIENT_ID` and `ZERO_TOUCH_SECRET_KEY` from your `.env` file to authenticate. On success, the button turns green and shows **"Connected"**.

[screenshot: sidebar showing green "Connected" button]

> If login fails, check your `.env` file and verify the credentials are correct.

### 9.3 Step 2 — Select Account

After login, the **Account** dropdown populates with your Zero Touch accounts. Select the account that contains your gateway templates.

[screenshot: account dropdown expanded]

### 9.4 Step 3 — Choose Gateway Type

Select the gateway type:

- **Spark (Quantum Spark)** — For 1500 series appliances (1570, 1590, etc.)
- **Full-Gaia (Quantum Force)** — For 3000–6000 series appliances (3950, 3970, etc.)

This filters the template list to show only matching templates.

[screenshot: gateway type dropdown]

### 9.5 Step 4 — Select Template

Choose the Zero Touch template that matches your gateway hardware. Templates are filtered based on the gateway type selected:

- **Spark** templates: names containing "Spark"
- **Gaia** templates: names not containing "Spark"

[screenshot: template dropdown showing filtered templates]

### 9.6 Step 5 — Enter MAC Address and Gateway Name

- **MAC Address** — Enter the physical MAC address of the gateway (format: `00:1C:7F:XX:XX:XX`)
- **Gateway Name** — Enter a hostname for the gateway (alphanumeric, hyphens, underscores only)

[screenshot: MAC address and gateway name fields filled in]

### 9.7 Step 6 — Select Management Platform

Choose the management platform:

| Platform | When to Use |
|---|---|
| **Smart-1 Cloud** | Gateway managed by Smart-1 Cloud (MaaS) |
| **MDS / SMS** | Gateway managed by an on-premises SMS or MDS |
| **LSM** | Gateway managed via Large Scale Management profiles |
| **SMP** | Spark-only — managed entirely by the Spark Management Portal |

> **Note:** SMP only appears when gateway type is set to "Spark".

[screenshot: management platform dropdown]

### 9.8 Step 7 — Claim the Gateway

Once all sidebar fields are complete, the **"Claim Gateway"** button appears. Click it to claim the gateway in the Zero Touch Portal.

Claiming:
- Associates the MAC address with the selected template
- Retrieves the template's user-script for review
- For Gaia gateways, populates the Network Configuration tab with template defaults

[screenshot: claim button and "Claimed" status badge]

### 9.9 Step 8 — Configure Deployment Parameters

After claiming, the main area switches to a **tabbed interface**. The first tab shows platform-specific configuration:

**Smart-1 Cloud:**
- SIC One-Time Password (required, min 4 characters)
- Hardware selection (auto-detect or manual)
- OS Version (Spark: R81.10 recommended)
- Smart-1 Cloud IP (auto-generate or specific)
- Security Blades (checkboxes for Firewall, VPN, IPS, App Control, URL Filtering, Anti-Bot, Anti-Virus, Threat Emulation, Content Awareness)
- VPN Community and Role (optional)
- Policy Package (optional)
- Open activation link toggle (Gaia only)

[screenshot: Smart-1 Cloud configuration tab]

**SMS / MDS:**
- Management Server IP (required — also triggers hardware/version list fetch)
- Domain (for MDS)
- Gateway IPv4 Address (required)
- SIC One-Time Password (required)
- Hardware and OS Version (populated from management server capabilities)
- Security Blades (checkboxes)
- VPN Community and Role
- Policy Package
- Open activation link toggle (Gaia only)

[screenshot: SMS configuration tab]

**LSM:**
- Management Server IP
- SIC One-Time Password (required)
- Security Profile (required — must exist on management server)
- Provisioning Profile (required — must exist on management server)
- Gateway IPv4 (optional — overrides template default)
- Domain (for MDS)

[screenshot: LSM configuration tab]

**SMP:**
- No additional configuration needed — informational message displayed

[screenshot: SMP configuration tab]

### 9.10 Step 9 — Review the User Script

Switch to the **"User Script"** tab to review the clish script that will be pushed to Zero Touch.

Key features:
- **Live placeholder preview** — placeholders like `<gateway-name>`, `<sic-key>`, `<mgmt-server-ip>` are highlighted and show their resolved values
- **Unresolved placeholders** — a warning badge on the tab indicates how many placeholders are not yet resolved
- **Editable** — you can edit the script directly in the text area
- **Copy button** — copies the processed script to clipboard

[screenshot: user script tab showing placeholders]

### 9.11 Step 10 — Network Configuration (Gaia Only)

For Gaia gateways, a **"Network Configuration"** tab is available. This sets the gateway's management interface settings during FTW:

- Management IPv4 Address and Subnet Mask
- Default Gateway
- DNS Servers (up to 3)
- NTP Servers (default: ntp.checkpoint.com)
- Timezone
- Admin Password
- Proxy Settings (optional)
- IPv6 Configuration (optional)
- Upload/Download Info toggles

These values are pre-populated from the Zero Touch template defaults when the gateway is claimed. Modify per-gateway as needed.

[screenshot: network configuration tab for Gaia gateway]

### 9.12 Step 11 — Deploy

Click the **"Deploy"** button at the bottom of the configuration tab.

Before deploying:
1. The application pushes the processed user-script to Zero Touch
2. For Gaia gateways, network configuration is also pushed
3. The deployment request is sent to the backend orchestrator

[screenshot: deploy button enabled]

### 9.13 Step 12 — Monitor the Deployment Log

The view automatically switches to the **"Deployment Log"** tab. A real-time log shows each step as it completes via Server-Sent Events (SSE):

- **Step-by-step progress** — each operation (login, claim, create gateway, establish SIC, install policy, etc.) is logged
- **Elapsed timer** — shows total deployment time
- **Status indicators** — color-coded per step (in-progress, success, error)
- **Final result** — success message with blade summary, or error details

[screenshot: deployment log showing successful deployment steps]

**For Gaia gateways (SMS/S1C):** If "Open activation link" was enabled, the activation link opens automatically in a new browser tab after successful deployment.

### 9.14 Unclaiming a Gateway

If you need to start over or release a gateway, click the **"Unclaim Gateway"** button in the sidebar. This removes the gateway claim in Zero Touch and resets the deployment state.

### 9.15 Resetting the Workflow

Click **"Reset"** in the sidebar footer to clear all state and start fresh with a new login.

---

## 10. Batch Deployment with deploy-batch.py

The `deploy-batch.py` script deploys multiple gateways from a CSV file in a single run. The backend server must be running.

### 10.1 Basic Usage

```bash
# Deploy all gateways from a Smart-1 Cloud CSV
python deploy-batch.py -t s1c --csv sample_smart1_cloud.csv

# Deploy all gateways from an SMS CSV
python deploy-batch.py -t sms --csv sample_sms.csv

# Deploy all gateways from an LSM CSV
python deploy-batch.py -t lsm --csv sample_lsm.csv

# Deploy all gateways from an SMP CSV
python deploy-batch.py -t smp --csv sample_smp.csv
```

**Deployment type shortcuts:**

| Short | Full Name |
|---|---|
| `s1c` | Smart-1 Cloud |
| `sms` | SMS / MDS |
| `lsm` | LSM |
| `smp` | SMP |

### 10.2 Filtering a Single Gateway

Deploy only one gateway by name:

```bash
python deploy-batch.py -t sms --csv sample_sms.csv --filter gw-3970-01
```

### 10.3 Replacing a Gateway (MAC Override)

When replacing a physical unit, override the MAC address for a specific gateway:

```bash
python deploy-batch.py -t lsm --csv sample_lsm.csv --filter gw-3950-01 --set-mac 00:1C:7F:AB:CD:EF
```

### 10.4 Dry Run Validation

Validate the CSV without actually deploying:

```bash
python deploy-batch.py -t sms --csv sample_sms.csv --dry-run
```

This parses the CSV, checks for required fields, and prints what would be deployed.

### 10.5 Exporting Results to CSV

Save deployment results (success/failure, activation links, duration) to a CSV file:

```bash
python deploy-batch.py -t sms --csv sample_sms.csv --output results.csv
```

**Output columns:**

| Column | Description |
|---|---|
| `gateway_name` | Gateway hostname |
| `mac_address` | Gateway MAC |
| `deployment_type` | Flow used |
| `success` | `True` / `False` |
| `message` | Human-readable outcome |
| `activation_link` | Zero Touch activation URL (Gaia) |
| `duration_seconds` | Deployment time |
| `error` | Error message if failed |

### 10.6 Remote Backend

Point the batch tool at a remote backend server:

```bash
python deploy-batch.py -t sms --csv sample_sms.csv --api-url https://192.168.1.100
```

### 10.7 Creating Sample CSV Templates

Generate blank sample CSVs with the correct headers:

```bash
# Create a sample for a specific type
python deploy-batch.py --create-sample sms

# Create samples for all types
python deploy-batch.py --create-all-samples
```

---

## 11. Lab Exercises

### Exercise 1 — Deploy a Spark Gateway to Smart-1 Cloud

**Objective:** Deploy a 1590 appliance managed by Smart-1 Cloud.

**Steps:**
1. Verify `.env` has `ZERO_TOUCH_CLIENT_ID`, `ZERO_TOUCH_SECRET_KEY`, and `SMART1_CLOUD_*` settings
2. Start the backend: `python -m app.main`
3. Open the web UI at http://localhost:8000
4. Login to Zero Touch Portal
5. Select your account
6. Set gateway type to **Spark**
7. Select a Spark template (e.g., `Master_S1C-Spark`)
8. Enter the gateway MAC address and name (e.g., `gw-1590-01`)
9. Set management platform to **Smart-1 Cloud**
10. Click **Claim Gateway**
11. Configure: SIC OTP = `vpn123`, select hardware, enable desired blades
12. Review the user script — verify `<sic-key>` and `<token>` placeholders are present
13. Click **Deploy**
14. Monitor the deployment log until completion

**Expected outcome:** Gateway created in Smart-1 Cloud with MaaS token injected. Activation link opened (if Gaia).

---

### Exercise 2 — Deploy a Gaia Gateway to SMS

**Objective:** Deploy a 3950 appliance managed by an on-premises SMS/MDS.

**Steps:**
1. Verify `.env` has `MGMT_BASE_URL` and `MGMT_SERVER_API_KEY` configured
2. Open the web UI
3. Login → Select account → Set type to **Full-Gaia** → Select a Gaia template
4. Enter MAC and gateway name (e.g., `gw-3950-01`)
5. Set management platform to **MDS / SMS**
6. Claim the gateway
7. Enter Management Server IP → hardware and version lists auto-populate
8. Enter Gateway IPv4 (e.g., `192.168.10.81`), SIC OTP, select hardware and version
9. Configure blades and VPN community
10. Review the **Network Configuration** tab — verify management IPv4, subnet, default gateway, DNS
11. Review the user script
12. Click **Deploy**
13. Watch the deployment log — observe SIC establishment polling and policy installation

**Expected outcome:** Gateway object created on management server, SIC established, blades configured, policy installed, activation link opened.

---

### Exercise 3 — Batch Deploy Multiple Gateways to LSM

**Objective:** Deploy 4 gateways to LSM using the batch CLI tool.

**Steps:**
1. Edit `sample_lsm.csv` with your gateway MAC addresses, management server IP, and LSM profiles
2. Verify LSM security profiles and provisioning profiles exist on the management server
3. Run a dry-run first:
   ```bash
   python deploy-batch.py -t lsm --csv sample_lsm.csv --dry-run
   ```
4. Deploy all gateways:
   ```bash
   python deploy-batch.py -t lsm --csv sample_lsm.csv --output lsm_results.csv -v
   ```
5. Review `lsm_results.csv` for results

**Expected outcome:** All gateways claimed, LSM gateway objects created, published, and released for deployment.

---

### Exercise 4 — SMP Zero Touch Only Deployment

**Objective:** Deploy Spark gateways to SMP with no management server interaction.

**Steps:**
1. Edit `sample_smp.csv` with your gateway MAC addresses
2. Via Web UI: Login → select Spark → select SMP template → enter MAC + name → set platform to **SMP** → Claim → Deploy
3. Or via CLI:
   ```bash
   python deploy-batch.py -t smp --csv sample_smp.csv
   ```

**Expected outcome:** Gateways claimed and released in Zero Touch. They will connect to SMP automatically when powered on.

---

## 12. Logging and Debugging

### Log File Location

Logs are written to `backend/logs/app.log`.

- **Rotation:** 10 MB per file
- **Retention:** 7 days
- **Compression:** Rotated files are gzip-compressed

### Log Level Configuration

In `.env`:

```ini
# Standard operational logging
LOG_LEVEL=INFO

# Verbose debugging (includes HTTP details)
LOG_LEVEL=DEBUG

# Warnings and errors only
LOG_LEVEL=WARNING
```

### API Body Logging

Control HTTP request/response body logging separately:

```ini
# No body logging (default)
API_DEBUG=none

# Log request bodies only
API_DEBUG=req

# Log response bodies only
API_DEBUG=resp

# Log both (useful for debugging API failures)
API_DEBUG=all
```

### Console Logging

Enable real-time console output (useful during development):

```ini
DEBUG=true
```

### Viewing Logs

```bash
# Follow the log file in real time
tail -f backend/logs/app.log

# View with systemd journal (if using systemd service)
sudo journalctl -u gw-deployer -f

# View Nginx logs
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

### Sensitive Data in Logs

By default, passwords, SIC keys, and tokens are redacted (`***`) in log output. To see them in plain text for debugging:

```ini
SHOW_SECRETS_IN_LOGFILE=true
```

> **Warning:** Disable this in production environments.

---

## 13. API Reference (Quick Summary)

Interactive API documentation is available at **http://localhost:8000/docs** (Swagger UI).

### Deployment Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/deployment/deploy-with-smart1-cloud/stream` | Smart-1 Cloud deployment (SSE) |
| POST | `/api/deployment/deploy-with-sms/stream` | SMS deployment (SSE) |
| POST | `/api/deployment/deploy-with-lsm/stream` | LSM deployment (SSE) |
| POST | `/api/deployment/deploy-with-smp/stream` | SMP deployment (SSE) |

### Zero Touch Portal Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/zero-touch/login` | Authenticate to Zero Touch |
| GET | `/api/zero-touch/accounts` | List accounts |
| GET | `/api/zero-touch/templates` | List templates |
| POST | `/api/zero-touch/gateways/claim` | Claim a gateway |
| DELETE | `/api/zero-touch/gateways/{mac}` | Unclaim a gateway |

### SSE Event Types

| Event | Description |
|---|---|
| `status` | Step progress update with `step`, `message`, `status` |
| `complete` | Deployment finished — contains `success`, `gateway_name`, `activation_link` |
| `error` | Deployment failed — contains `error` message |
| `heartbeat` | Keep-alive ping (every 120s) |

---

## 14. Troubleshooting

### Zero Touch Login Fails

| Possible Cause | Solution |
|---|---|
| Wrong credentials | Verify `ZERO_TOUCH_CLIENT_ID` and `ZERO_TOUCH_SECRET_KEY` in `.env` |
| Network issue | Ensure the deployer host can reach `zerotouch.checkpoint.com` (HTTPS) |
| Insufficient permissions | Verify the API credentials have gateway management permissions |

### Claim Fails

| Possible Cause | Solution |
|---|---|
| MAC already claimed | Check if the gateway is already claimed in another account/template. Unclaim first. |
| Invalid `template_id` | Verify the template ID matches the portal and the hardware model |
| LSM claim failure | LSM claim fails hard — verify MAC and `template_id` before running |

### SIC Does Not Establish (test-trust Timeout)

| Possible Cause | Solution |
|---|---|
| Gateway not online | Power on the gateway and wait for FTW to complete |
| Port 18191 blocked | Ensure TCP 18191 is open between the management server and `gateway_ipv4` |
| Wrong `gateway_ipv4` | Verify the IP matches what the device actually uses |
| SIC key mismatch | For Gaia: ensure `ftw-sic-key` (Zero Touch) and `one-time-password` (management object) are identical |
| Timeout too short | Increase `SIC_TIMEOUT` in `.env` (default: 900 seconds) |

### Content Awareness Error on Spark

Content Awareness is **not supported** on 1500 series (SMB) gateways. Set `enable_content_awareness=false` in the CSV or leave the checkbox unchecked in the web UI.

### Management Server Not Reachable

| Possible Cause | Solution |
|---|---|
| Wrong `MGMT_BASE_URL` | Verify the URL format: `https://<ip>:443/web_api/` |
| Expired API key | Generate a new API key on the management server |
| Firewall blocking | Open port 443 between the deployer host and the management server |

### CORS Errors in Browser

Update `ALLOWED_ORIGINS` in `.env` to match the URL you are accessing the application from. Restart the backend after changes.

### Deployment Appears Stuck

- Check `backend/logs/app.log` for detailed progress
- SSE streams have a 15-minute timeout — SIC establishment can take several minutes
- The Nginx `proxy_read_timeout` must be set to at least `900s`

### Wrong IP Sent to Zero Touch

Each gateway must have its own `gateway_ipv4` in the CSV. If left as the template default, all gateways sharing a template will receive the same IP.

---

## 15. Security Considerations

### Credentials Management

- **Never commit `.env` to version control** — add it to `.gitignore`
- Change `SECRET_KEY` and `SESSION_SECRET` from their defaults in production
- Use `SHOW_SECRETS_IN_LOGFILE=false` (default) in production

### Network Security

- Run behind Nginx with HTTPS (see [Section 6](#6-nginx-reverse-proxy-and-https))
- Bind the backend to `127.0.0.1` when using a reverse proxy
- Block direct access to port 8000 from outside the host

### TLS Verification

`SSL_VERIFY=false` is set by default for convenience with self-signed certificates on management servers. In production environments with proper certificates, set:

```ini
SSL_VERIFY=true
```

### SIC Passwords

SIC one-time passwords (`sic_otp`) are used once during gateway initialization. Use strong, unique passwords for each gateway. They are redacted in log output by default.

### API Keys

- Zero Touch API credentials grant access to claim and configure gateways
- Management server API keys grant full read/write access
- Rotate these credentials periodically

---

## 16. Appendix — Config File Injection Examples

### Example: Static Routes

**File:** `backend/config_files/gw-3950-01/routing-table.conf`

```clish
set static-route 10.0.0.0/8 nexthop gateway address 192.168.1.1 on
set static-route 172.16.0.0/12 nexthop gateway address 192.168.1.1 on
```

**In the template user-script:**
```clish
set hostname <gateway-name>
##!! routing-table.conf
```

**Result after injection (for gw-3950-01):**
```clish
set hostname gw-3950-01
set static-route 10.0.0.0/8 nexthop gateway address 192.168.1.1 on
set static-route 172.16.0.0/12 nexthop gateway address 192.168.1.1 on
```

### Example: Shared Configuration

**File:** `backend/config_files/dns-settings.conf` (shared — used by all gateways)

```clish
set dns primary 8.8.8.8
set dns secondary 8.8.4.4
set dns suffix example.com
```

### Example: Per-Gateway Override

If `gw-3950-01` needs different DNS:
- `backend/config_files/gw-3950-01/dns-settings.conf` → used for gw-3950-01
- `backend/config_files/dns-settings.conf` → used for all other gateways

---

## Additional Suggestions for Lab Expansion

Here are additional topics you may want to include as the lab evolves:

- **Multi-Domain Server (MDS) Lab** — walkthrough of deploying gateways across multiple domains, including domain selection in the CSV and web UI
- **Gateway Replacement Procedure** — step-by-step for replacing a failed gateway (unclaim old MAC, deploy with `--set-mac`)
- **Template Design Best Practices** — how to structure Zero Touch templates for reusability across hardware models
- **Monitoring Deployment with the API Docs** — using the Swagger UI at `/docs` to test individual endpoints
- **Integration with CI/CD** — using `deploy-batch.py` in automated pipelines (Jenkins, GitHub Actions)
- **Log Analysis** — reading and interpreting `app.log` entries for deployment forensics
- **Scaling the Deployment** — running multiple batch deployments in parallel, tuning `SIC_TIMEOUT`
- **Backup and Recovery** — exporting deployment results, maintaining a gateway inventory
- **Custom User-Script Development** — authoring advanced clish scripts with file injection for specific use cases
