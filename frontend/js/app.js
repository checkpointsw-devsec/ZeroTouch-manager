const { createApp } = Vue;

createApp({
    data() {
        return {
            // ── State flags ──
            authenticated: false,
            loading: false,
            deploying: false,
            claimed: false,
            claiming: false,
            unclaiming: false,

            // ── Sidebar ──
            accounts: [],
            selectedAccountId: '',
            selectedAccount: null,
            allTemplates: [],
            filteredTemplates: [],
            selectedTemplateId: '',
            selectedTemplate: null,
            gatewayType: '',            // 'spark' | 'gaia'
            macAddress: '',
            managementPlatform: '',     // 'smart1-cloud' | 'sms' | 'lsm' | 'smp'

            // ── Gateway details ──
            gatewayName: '',
            timezone: 'UTC',
            ipAssignment: 'auto',
            fixedIp: '',
            userScript: '',

            // ── Gaia Network Configuration ──
            gaiaNetwork: {
                mgmt_eth_ip_address_ipv4: '',
                mgmt_eth_subnet_mask_ipv4: '',
                default_gateway_ipv4: '',
                dns_server1: '',
                dns_server2: '',
                dns_server3: '',
                ntp1: 'ntp.checkpoint.com',
                ntp1_version: '4',
                ntp2: 'ntp2.checkpoint.com',
                ntp2_version: '4',
                timezone: 'UTC',
                admin_password: '',
                show_admin_password: false,
                proxy_server: '',
                proxy_port: 8080,
                config_ipv6: false,
                mgmt_eth_ip_address_ipv6: '',
                mgmt_eth_mask_length_ipv6: '',
                default_gateway_ipv6: '',
                upload_info: true,
                download_info: true
            },

            // ── Smart-1 Cloud ──
            s1c: {
                sicKey: '',
                showSicKey: false,
                hardware: '',
                hardwareOptions: [],
                hardwareLoading: false,
                osVersion: 'R81.10',
                autoGenerateIp: true,
                ipAddress: '',
                firewall: true,
                vpn: true,
                ips: true,
                applicationControl: true,
                urlFiltering: true,
                antiBot: true,
                antiVirus: true,
                threatEmulation: true,
                contentAwareness: false,
                vpnCommunity: '',
                vpnRole: 'satellite',
                policyName: '',
                openActivationLink: true
            },

            // ── LSM ──
            lsm: {
                mgmtServerIp: '',
                sicKey: '',
                showSicKey: false,
                securityProfile: '',
                provisioningProfile: '',
                domain: '',
                gatewayIpv4: ''
            },

            // ── SMS / MDS ──
            sms: {
                mgmtServerIp: '',
                sicKey: '',
                showSicKey: false,
                gatewayIpv4: '',
                hardware: '',
                version: '',
                policyName: 'Standard',
                enableAppControl: true,
                enableIps: true,
                enableUrlFiltering: false,
                enableContentAwareness: false,
                enableIpsec: true,
                enableAntiBot: true,
                enableAntiVirus: true,
                enableThreatEmulation: true,
                vpnCommunity: '',
                vpnRole: 'satellite',
                domain: '',
                openActivationLink: true,
                hardwareOptions: [],
                versionOptions: [],
                capabilitiesLoading: false
            },

            // ── Deployment ──
            deploymentLog: [],
            deploymentStatus: {
                show: false,
                type: 'info',
                title: '',
                message: '',
                step: null,
                elapsed: '',
                startTime: null
            },
            pendingOpenActivationLink: true,

            // ── SD-WAN ──
            sdwan: {
                enabled: false,
                profile: ''
            },

            // ── Alert ──
            alert: { show: false, type: 'info', message: '' }
        };
    },

    computed: {
        macAddressValid() {
            return /^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/.test(this.macAddress.trim());
        },
        sidebarReady() {
            return this.authenticated
                && this.selectedAccount
                && this.gatewayType
                && this.selectedTemplate
                && this.macAddressValid
                && !!this.gatewayName.trim()
                && !!this.managementPlatform;
        },
        canDeploy() {
            if (!this.sidebarReady || !this.gatewayName.trim() || !this.claimed) return false;
            switch (this.managementPlatform) {
                case 'smart1-cloud':
                    return this.s1c.sicKey.length >= 4;
                case 'lsm':
                    return this.lsm.sicKey.length >= 4
                        && !!this.lsm.securityProfile.trim()
                        && !!this.lsm.provisioningProfile.trim();
                case 'sms':
                    return this.sms.sicKey.length >= 4
                        && !!this.sms.mgmtServerIp.trim()
                        && !!this.sms.gatewayIpv4.trim()
                        && !!this.sms.hardware
                        && !!this.sms.version;
                case 'smp':
                    return true;
                default:
                    return false;
            }
        },
        managementPlatformLabel() {
            const map = {
                'smart1-cloud': 'Smart-1 Cloud',
                'sms': 'MDS / SMS',
                'lsm': 'LSM',
                'smp': 'SMP'
            };
            return map[this.managementPlatform] || '';
        },

        // SD-WAN profile assignment is only supported for Smart-1 Cloud and MDS/SMS
        sdwanAvailable() {
            return this.managementPlatform === 'smart1-cloud' || this.managementPlatform === 'sms';
        },

        processedUserScript() {
            let script = this.userScript;
            if (!script) return '';
            // Replace <gateway-name> (may already be done by backend)
            if (this.gatewayName) {
                script = script.replaceAll('<gateway-name>', this.gatewayName);
            }
            // Determine SIC key and mgmt IP by platform
            let sicKey = '';
            let mgmtIp = '';
            switch (this.managementPlatform) {
                case 'smart1-cloud': sicKey = this.s1c.sicKey; break;
                case 'lsm': sicKey = this.lsm.sicKey; mgmtIp = this.lsm.mgmtServerIp; break;
                case 'sms': sicKey = this.sms.sicKey; mgmtIp = this.sms.mgmtServerIp; break;
            }
            if (sicKey) {
                script = script.replaceAll('<sic-key>', sicKey);
                script = script.replaceAll('<ftw-sic-key>', sicKey);
            }
            if (mgmtIp) {
                script = script.replaceAll('<mgmt-server-ip>', mgmtIp);
            }
            return script;
        },

        detectedPlaceholders() {
            if (!this.userScript) return [];
            const found = [];
            let sicKey = '';
            let mgmtIp = '';
            switch (this.managementPlatform) {
                case 'smart1-cloud': sicKey = this.s1c.sicKey; break;
                case 'lsm': sicKey = this.lsm.sicKey; mgmtIp = this.lsm.mgmtServerIp; break;
                case 'sms': sicKey = this.sms.sicKey; mgmtIp = this.sms.mgmtServerIp; break;
            }
            if (this.userScript.includes('<sic-key>'))
                found.push({ tag: '<sic-key>', resolved: !!sicKey, value: sicKey ? '***' : '' });
            if (this.userScript.includes('<ftw-sic-key>'))
                found.push({ tag: '<ftw-sic-key>', resolved: !!sicKey, value: sicKey ? '***' : '' });
            if (this.userScript.includes('<mgmt-server-ip>'))
                found.push({ tag: '<mgmt-server-ip>', resolved: !!mgmtIp, value: mgmtIp || '' });
            if (this.userScript.includes('<gateway-name>'))
                found.push({ tag: '<gateway-name>', resolved: !!this.gatewayName, value: this.gatewayName || '' });
            if (this.userScript.includes('<token>'))
                found.push({ tag: '<token>', resolved: false, value: 'auto (during deployment)' });
            return found;
        }
    },

    methods: {
        // ────────────────────────── Alerts ──────────────────────────
        showAlert(type, message) {
            this.alert = { show: true, type, message };
            setTimeout(() => { this.alert.show = false; }, 6000);
        },

        // ────────────────────────── Authentication ──────────────────────────
        async login() {
            this.loading = true;
            try {
                const r = await fetch('/api/zero-touch/login', { method: 'POST' });
                if (!r.ok) throw new Error('Login failed');
                this.authenticated = true;
                this.showAlert('success', 'Logged in to Zero Touch Portal');
                await this.loadAccounts();
            } catch (e) {
                this.showAlert('danger', 'Login failed: ' + e.message);
            } finally {
                this.loading = false;
            }
        },

        // ────────────────────────── Data Loading ──────────────────────────
        async loadAccounts() {
            try {
                const r = await fetch('/api/zero-touch/accounts');
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                this.accounts = Array.isArray(data) ? data : (data.accounts || []);
                console.log('Loaded accounts:', this.accounts.length);
            } catch (e) {
                this.showAlert('danger', 'Failed to load accounts: ' + e.message);
            }
        },

        onAccountSelect() {
            this.selectedAccount = this.accounts.find(a => String(a.id) === String(this.selectedAccountId)) || null;
            // Reset downstream selections
            this.gatewayType = '';
            this.selectedTemplateId = '';
            this.selectedTemplate = null;
            this.filteredTemplates = [];
            this.allTemplates = [];
            this.macAddress = '';
            this.managementPlatform = '';
            if (this.selectedAccount) {
                this.loadTemplates();
            }
        },

        async loadTemplates() {
            try {
                const r = await fetch(`/api/zero-touch/templates?account_id=${this.selectedAccount.id}`);
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                this.allTemplates = Array.isArray(data) ? data : (data.templates || []);
                console.log('Loaded templates:', this.allTemplates.length);
                this.filterTemplates();
            } catch (e) {
                this.showAlert('danger', 'Failed to load templates: ' + e.message);
            }
        },

        filterTemplates() {
            if (this.gatewayType === 'spark') {
                this.filteredTemplates = this.allTemplates.filter(t =>
                    t.name && t.name.toLowerCase().includes('spark')
                );
            } else if (this.gatewayType === 'gaia') {
                this.filteredTemplates = this.allTemplates.filter(t =>
                    t.name && !t.name.toLowerCase().includes('spark')
                );
            } else {
                this.filteredTemplates = [];
            }
            // Reset template selection if current is no longer in filtered list
            if (this.selectedTemplate && !this.filteredTemplates.find(t => t.id === this.selectedTemplate.id)) {
                this.selectedTemplateId = '';
                this.selectedTemplate = null;
            }
        },

        onTemplateSelect() {
            this.selectedTemplate = this.filteredTemplates.find(t => String(t.id) === String(this.selectedTemplateId)) || null;
        },

        // ────────────────────────── Hardware / Capability Fetching ──────────────────────────
        async fetchHardwareOptions() {
            this.s1c.hardwareLoading = true;
            this.s1c.hardwareOptions = [];
            try {
                const r = await fetch('/api/smart1-cloud/hardware-options');
                if (r.ok) {
                    const data = await r.json();
                    if (data.success && data.hardware) {
                        this.s1c.hardwareOptions = data.hardware;
                    }
                }
            } catch (e) {
                console.warn('Error fetching hardware options:', e);
            } finally {
                this.s1c.hardwareLoading = false;
            }
        },

        async fetchSmsGatewayCapabilities() {
            if (!this.sms.mgmtServerIp) return;
            const platform = this.gatewayType === 'spark' ? 'smb' : 'quantum';
            this.sms.capabilitiesLoading = true;
            this.sms.hardwareOptions = [];
            this.sms.versionOptions = [];
            try {
                const r = await fetch('/api/deployment/sms-gateway-capabilities', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mgmt_server_ip: this.sms.mgmtServerIp, platform })
                });
                if (r.ok) {
                    const data = await r.json();
                    if (data.success) {
                        this.sms.hardwareOptions = data.hardware || [];
                        this.sms.versionOptions = data.versions || [];
                    }
                }
            } catch (e) {
                console.warn('Error fetching SMS capabilities:', e);
            } finally {
                this.sms.capabilitiesLoading = false;
            }
        },

        // ────────────────────────── Claim ──────────────────────────
        async claimGateway() {
            if (this.claiming || !this.gatewayName.trim()) return;
            this.claiming = true;
            try {
                const r = await fetch('/api/zero-touch/gateways/claim', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mac_address: this.macAddress.toUpperCase(),
                        template_id: this.selectedTemplate.id,
                        gateway_name: this.gatewayName.trim(),
                        account_id: this.selectedAccount.id,
                        custom_settings: { under_construction: true }
                    })
                });
                if (!r.ok) {
                    let detail = 'Claim failed';
                    try { const err = await r.json(); detail = err.detail || JSON.stringify(err); } catch(_) {}
                    throw new Error(detail);
                }
                const data = await r.json();
                // Extract user-script from the claimed gateway configuration
                // Spark: nested under data.data['gateway-configuration']
                // Gaia:  flat at data.data top level
                let script = '';
                if (data.data) {
                    if (data.data['gateway-configuration']) {
                        script = data.data['gateway-configuration']['user-script'] || '';
                    } else if (data.data['user-script']) {
                        script = data.data['user-script'] || '';
                    }
                }
                this.userScript = script;

                // For Gaia gateways, populate gaiaNetwork from claim response
                if (this.gatewayType === 'gaia' && data.data) {
                    const d = data.data;
                    this.gaiaNetwork.mgmt_eth_ip_address_ipv4 = d['mgmt-eth-ip-address-ipv4'] || '';
                    this.gaiaNetwork.mgmt_eth_subnet_mask_ipv4 = d['mgmt-eth-subnet-mask-ipv4'] || '';
                    this.gaiaNetwork.default_gateway_ipv4 = d['default-gateway-ipv4'] || '';
                    this.gaiaNetwork.dns_server1 = d['dns-server1'] || '';
                    this.gaiaNetwork.dns_server2 = d['dns-server2'] || '';
                    this.gaiaNetwork.dns_server3 = d['dns-server3'] || '';
                    this.gaiaNetwork.ntp1 = d['ntp1'] || 'ntp.checkpoint.com';
                    this.gaiaNetwork.ntp1_version = d['ntp1-version'] || '4';
                    this.gaiaNetwork.ntp2 = d['ntp2'] || 'ntp2.checkpoint.com';
                    this.gaiaNetwork.ntp2_version = d['ntp2-version'] || '4';
                    this.gaiaNetwork.timezone = d['time-zone'] || 'UTC';
                    this.gaiaNetwork.admin_password = ''; // masked by API, don't populate
                    this.gaiaNetwork.proxy_server = d['proxy-server'] || '';
                    this.gaiaNetwork.proxy_port = d['proxy-port'] || 8080;
                    this.gaiaNetwork.config_ipv6 = !!d['config-ipv6'];
                    this.gaiaNetwork.mgmt_eth_ip_address_ipv6 = d['mgmt-eth-ip-address-ipv6'] || '';
                    this.gaiaNetwork.mgmt_eth_mask_length_ipv6 = d['mgmt-eth-mask-length-ipv6'] || '';
                    this.gaiaNetwork.default_gateway_ipv6 = d['default-gateway-ipv6'] || '';
                    this.gaiaNetwork.upload_info = d['upload-info'] !== undefined ? d['upload-info'] : true;
                    this.gaiaNetwork.download_info = d['download-info'] !== undefined ? d['download-info'] : true;
                    console.log('Populated gaiaNetwork from claim response');
                }

                this.claimed = true;
                this.showAlert('success', `Gateway "${this.gatewayName}" claimed successfully`);
            } catch (e) {
                this.showAlert('danger', 'Claim failed: ' + e.message);
            } finally {
                this.claiming = false;
            }
        },

        async unclaimGateway() {
            if (this.unclaiming) return;
            this.unclaiming = true;
            try {
                const mac = encodeURIComponent(this.macAddress.toUpperCase());
                const accountId = encodeURIComponent(this.selectedAccount.id);
                const templateName = encodeURIComponent(this.selectedTemplate.name);
                const url = `/api/zero-touch/gateways/${mac}?account_id=${accountId}&template_name=${templateName}`;
                const r = await fetch(url, { method: 'DELETE' });
                if (!r.ok) {
                    let detail = 'Unclaim failed';
                    try { const err = await r.json(); detail = err.detail || JSON.stringify(err); } catch(_) {}
                    throw new Error(detail);
                }
                this.claimed = false;
                this.userScript = '';
                this.deploymentLog = [];
                this.deploymentStatus = { show: false, type: 'info', title: '', message: '', step: null, elapsed: '', startTime: null };
                this.showAlert('success', `Gateway "${this.gatewayName}" unclaimed successfully`);
            } catch (e) {
                this.showAlert('danger', 'Unclaim failed: ' + e.message);
            } finally {
                this.unclaiming = false;
            }
        },

        async updateUserScriptInZT() {
            const mac = this.macAddress.toUpperCase();
            const accountId = this.selectedAccount.id;
            const templateName = this.selectedTemplate.name;
            const url = `/api/zero-touch/gateways/${encodeURIComponent(mac)}/configuration?account_id=${encodeURIComponent(accountId)}&template_name=${encodeURIComponent(templateName)}`;

            const payload = { 'user-script': this.processedUserScript };

            // For Gaia gateways, include network configuration fields
            if (this.gatewayType === 'gaia') {
                const n = this.gaiaNetwork;
                if (n.mgmt_eth_ip_address_ipv4) payload['mgmt-eth-ip-address-ipv4'] = n.mgmt_eth_ip_address_ipv4;
                if (n.mgmt_eth_subnet_mask_ipv4) payload['mgmt-eth-subnet-mask-ipv4'] = n.mgmt_eth_subnet_mask_ipv4;
                if (n.default_gateway_ipv4) payload['default-gateway-ipv4'] = n.default_gateway_ipv4;
                if (n.dns_server1) payload['dns-server1'] = n.dns_server1;
                if (n.dns_server2) payload['dns-server2'] = n.dns_server2;
                if (n.dns_server3) payload['dns-server3'] = n.dns_server3;
                payload['ntp1'] = n.ntp1 || 'ntp.checkpoint.com';
                payload['ntp1-version'] = n.ntp1_version || '4';
                payload['ntp2'] = n.ntp2 || 'ntp2.checkpoint.com';
                payload['ntp2-version'] = n.ntp2_version || '4';
                if (n.timezone) payload['time-zone'] = n.timezone;
                if (n.admin_password) payload['admin-password'] = n.admin_password;
                if (n.proxy_server) payload['proxy-server'] = n.proxy_server;
                if (n.proxy_server) payload['proxy-port'] = n.proxy_port;
                payload['config-ipv6'] = n.config_ipv6;
                if (n.config_ipv6) {
                    if (n.mgmt_eth_ip_address_ipv6) payload['mgmt-eth-ip-address-ipv6'] = n.mgmt_eth_ip_address_ipv6;
                    if (n.mgmt_eth_mask_length_ipv6) payload['mgmt-eth-mask-length-ipv6'] = n.mgmt_eth_mask_length_ipv6;
                    if (n.default_gateway_ipv6) payload['default-gateway-ipv6'] = n.default_gateway_ipv6;
                }
                payload['upload-info'] = n.upload_info;
                payload['download-info'] = n.download_info;
            }

            const r = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!r.ok) {
                const err = await r.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to update user script in Zero Touch');
            }
        },

        // ────────────────────────── Script Utils ──────────────────────────
        copyScript() {
            navigator.clipboard.writeText(this.processedUserScript || this.userScript);
            this.showAlert('success', 'Script copied to clipboard');
        },

        // ────────────────────────── Deploy Router ──────────────────────────
        async deploy() {
            if (!this.canDeploy || this.deploying) return;

            // Push the processed user-script to Zero Touch before deploying
            // so backend orchestrators read the user's reviewed/edited version
            if (this.userScript && this.managementPlatform !== 'smp') {
                try {
                    await this.updateUserScriptInZT();
                } catch (e) {
                    this.showAlert('danger', 'Failed to push user script: ' + e.message);
                    return;
                }
            }

            switch (this.managementPlatform) {
                case 'smart1-cloud': return this.deployToSmart1Cloud();
                case 'lsm':         return this.deployToLSM();
                case 'sms':         return this.deployToSMS();
                case 'smp':         return this.deployToSMP();
            }
        },

        // ────────────────────────── Smart-1 Cloud ──────────────────────────
        async deployToSmart1Cloud() {
            this.pendingOpenActivationLink = this.s1c.openActivationLink;

            const payload = {
                mac_address: this.macAddress.toUpperCase(),
                account_id: this.selectedAccount.id,
                template_name: this.selectedTemplate.name,
                gateway_name: this.gatewayName,
                user_script: this.processedUserScript || this.userScript || '',
                time_zone: this.timezone || 'UTC',
                sic_otp: this.s1c.sicKey,
                hardware: this.s1c.hardware || undefined,
                gateway_type: 'APPLIANCE_OR_OPENSERVER',
                identification_method: 'GATEWAY_NAME',
                os_version: this.s1c.osVersion,
                auto_generate_ip: this.s1c.autoGenerateIp,
                ip_address: this.s1c.autoGenerateIp ? undefined : this.s1c.ipAddress,
                firewall: this.s1c.firewall,
                vpn: this.s1c.vpn,
                ips: this.s1c.ips,
                application_control: this.s1c.applicationControl,
                url_filtering: this.s1c.urlFiltering,
                anti_bot: this.s1c.antiBot,
                anti_virus: this.s1c.antiVirus,
                threat_emulation: this.s1c.threatEmulation,
                content_awareness: this.s1c.contentAwareness,
                vpn_community: this.s1c.vpnCommunity || undefined,
                vpn_role: this.s1c.vpnCommunity ? this.s1c.vpnRole : undefined,
                policy_name: this.s1c.policyName || undefined,
                ipv4_address: (this.ipAssignment === 'fixed' && this.fixedIp) ? this.fixedIp : undefined
            };

            await this.streamDeploy(
                '/api/deployment/deploy-with-smart1-cloud/stream',
                payload,
                'Deploying to Smart-1 Cloud'
            );
        },

        // ────────────────────────── LSM ──────────────────────────
        async deployToLSM() {
            const payload = {
                mac_address: this.macAddress.toUpperCase(),
                account_id: this.selectedAccount.id,
                template_name: this.selectedTemplate.name,
                gateway_name: this.gatewayName,
                mgmt_server_ip: this.lsm.mgmtServerIp || undefined,
                sic_otp: this.lsm.sicKey,
                security_profile: this.lsm.securityProfile,
                provisioning_profile: this.lsm.provisioningProfile,
                gateway_ipv4: this.lsm.gatewayIpv4 || undefined
            };
            if (this.lsm.domain) payload.domain = this.lsm.domain;

            this.pendingOpenActivationLink = false;

            await this.streamDeploy(
                '/api/deployment/deploy-with-lsm/stream',
                payload,
                'Deploying to LSM'
            );
        },

        // ────────────────────────── SMS ──────────────────────────
        async deployToSMS() {
            this.pendingOpenActivationLink = this.sms.openActivationLink;

            const payload = {
                mac_address: this.macAddress.toUpperCase(),
                account_id: this.selectedAccount.id,
                template_name: this.selectedTemplate.name,
                gateway_name: this.gatewayName,
                mgmt_server_ip: this.sms.mgmtServerIp,
                sic_otp: this.sms.sicKey,
                gateway_ipv4: this.sms.gatewayIpv4,
                hardware: this.sms.hardware,
                version: this.sms.version,
                policy_name: this.sms.policyName || 'Standard',
                enable_app_control: this.sms.enableAppControl,
                enable_ips: this.sms.enableIps,
                enable_url_filtering: this.sms.enableUrlFiltering,
                enable_content_awareness: this.sms.enableContentAwareness,
                enable_ipsec: this.sms.enableIpsec,
                enable_anti_bot: this.sms.enableAntiBot,
                enable_anti_virus: this.sms.enableAntiVirus,
                enable_threat_emulation: this.sms.enableThreatEmulation
            };
            if (this.sms.vpnCommunity) {
                payload.vpn_community = this.sms.vpnCommunity;
                payload.vpn_role = this.sms.vpnRole;
            }
            if (this.sms.domain) payload.domain = this.sms.domain;

            await this.streamDeploy(
                '/api/deployment/deploy-with-sms/stream',
                payload,
                'Deploying to SMS'
            );
        },

        // ────────────────────────── SMP ──────────────────────────
        async deployToSMP() {
            this.pendingOpenActivationLink = false;

            const payload = {
                mac_address: this.macAddress.toUpperCase(),
                account_id: this.selectedAccount.id,
                template_name: this.selectedTemplate.name,
                gateway_name: this.gatewayName
            };

            await this.streamDeploy(
                '/api/deployment/deploy-with-smp/stream',
                payload,
                'Deploying to SMP'
            );
        },

        // ────────────────────────── Streaming SSE Engine ──────────────────────────
        async streamDeploy(url, payload, title) {
            this.deploying = true;
            this.deploymentLog = [];

            // Switch to the Deployment Log tab
            this.$nextTick(() => {
                const logTab = document.getElementById('tab-log');
                if (logTab) new bootstrap.Tab(logTab).show();
            });
            this.deploymentStatus = {
                show: true,
                type: 'progress',
                title: title,
                message: 'Initializing deployment...',
                step: 0,
                elapsed: '0:00',
                startTime: Date.now()
            };

            // Elapsed timer
            const elapsedInterval = setInterval(() => {
                if (this.deploymentStatus.startTime) {
                    const s = Math.floor((Date.now() - this.deploymentStatus.startTime) / 1000);
                    this.deploymentStatus.elapsed = `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;
                }
            }, 1000);

            try {
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    let detail = 'Deployment failed';
                    try {
                        const err = await response.json();
                        detail = err.detail || JSON.stringify(err);
                    } catch (_) { /* ignore parse error */ }
                    throw new Error(detail);
                }

                // Read SSE stream
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // keep incomplete line

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        let eventData;
                        try { eventData = JSON.parse(line.slice(6)); } catch (_) { continue; }

                        if (eventData.event === 'status') {
                            const d = eventData.data || {};
                            this.deploymentStatus.message = d.message || '';
                            this.deploymentStatus.step = d.step ?? this.deploymentStatus.step;
                            this.deploymentLog.push({
                                step: d.step ?? null,
                                message: d.message || '',
                                status: d.status || 'in_progress'
                            });
                            this.scrollLog();
                        } else if (eventData.event === 'complete') {
                            clearInterval(elapsedInterval);
                            this.handleComplete(eventData.data || {});
                            return;
                        } else if (eventData.event === 'error') {
                            clearInterval(elapsedInterval);
                            throw new Error((eventData.data || {}).error || 'Deployment failed');
                        }
                        // heartbeat — ignore
                    }
                }

            } catch (e) {
                clearInterval(elapsedInterval);
                this.deploymentStatus.type = 'error';
                this.deploymentStatus.title = 'Deployment Failed';
                this.deploymentStatus.message = e.message;
                this.deploymentLog.push({ step: null, message: 'ERROR: ' + e.message, status: 'error' });
                this.showAlert('danger', 'Deployment failed: ' + e.message);
            } finally {
                this.deploying = false;
            }
        },

        handleComplete(result) {
            if (result.success) {
                this.deploymentStatus.type = 'success';
                this.deploymentStatus.title = 'Deployment Complete';

                let msg = 'Gateway successfully deployed!';
                // Build blade summary for SMS
                const blades = [];
                if (result.security_blades) {
                    const sb = result.security_blades;
                    if (sb.firewall) blades.push('Firewall');
                    if (sb.vpn) blades.push('VPN');
                    if (sb.ips) blades.push('IPS');
                    if (sb.application_control) blades.push('App Control');
                    if (sb.url_filtering) blades.push('URL Filtering');
                    if (sb.content_awareness) blades.push('Content Awareness');
                    if (blades.length) msg += ' Enabled: ' + blades.join(', ');
                }
                this.deploymentStatus.message = msg;
                this.deploymentLog.push({ step: null, message: msg, status: 'completed' });
                this.showAlert('success', msg);

                // Activation link
                if (result.activation_link && this.pendingOpenActivationLink) {
                    setTimeout(() => window.open(result.activation_link, '_blank'), 2000);
                } else if (result.activation_link) {
                    console.log('Activation link:', result.activation_link);
                }

                // SD-WAN profile assignment (post-deployment)
                if (this.sdwanAvailable && this.sdwan.enabled && this.sdwan.profile.trim()) {
                    this.assignSdwanProfile();
                }
            } else {
                const errMsg = result.error || 'Deployment returned unsuccessful';
                this.deploymentStatus.type = 'error';
                this.deploymentStatus.title = 'Deployment Failed';
                this.deploymentStatus.message = errMsg;
                this.deploymentLog.push({ step: null, message: 'ERROR: ' + errMsg, status: 'error' });
                this.showAlert('danger', errMsg);
            }
        },

        scrollLog() {
            this.$nextTick(() => {
                const el = this.$refs.deploymentLog;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },

        // ────────────────────────── SD-WAN ──────────────────────────
        async assignSdwanProfile() {
            const profile = this.sdwan.profile.trim();
            this.deploymentLog.push({ step: null, message: `Assigning gateway to SD-WAN profile "${profile}"...`, status: 'in_progress' });
            this.scrollLog();
            try {
                const r = await fetch('/api/deployment/join-sdwan-profile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        gateway_name: this.gatewayName,
                        profile_name: profile
                    })
                });
                if (!r.ok) {
                    let detail = 'SD-WAN assignment failed';
                    try { const err = await r.json(); detail = err.detail || JSON.stringify(err); } catch (_) {}
                    throw new Error(detail);
                }
                const data = await r.json();
                (data.steps || []).forEach(s => {
                    this.deploymentLog.push({ step: null, message: 'SD-WAN: ' + s, status: 'completed' });
                });
                const okMsg = `Gateway assigned to SD-WAN profile "${profile}"`;
                this.deploymentLog.push({ step: null, message: okMsg, status: 'completed' });
                this.deploymentStatus.message = okMsg;
                this.showAlert('success', okMsg);
                this.scrollLog();
            } catch (e) {
                const errMsg = 'SD-WAN assignment failed: ' + e.message;
                this.deploymentLog.push({ step: null, message: errMsg, status: 'error' });
                this.showAlert('warning', errMsg);
                this.scrollLog();
            }
        },

        // ────────────────────────── Reset ──────────────────────────
        resetWorkflow() {
            this.authenticated = false;
            this.accounts = [];
            this.selectedAccountId = '';
            this.selectedAccount = null;
            this.allTemplates = [];
            this.filteredTemplates = [];
            this.selectedTemplateId = '';
            this.selectedTemplate = null;
            this.gatewayType = '';
            this.macAddress = '';
            this.managementPlatform = '';
            this.gatewayName = '';
            this.timezone = 'UTC';
            this.ipAssignment = 'auto';
            this.fixedIp = '';
            this.userScript = '';
            this.claimed = false;
            this.claiming = false;
            this.unclaiming = false;
            this.deploymentLog = [];
            this.deploymentStatus = { show: false, type: 'info', title: '', message: '', step: null, elapsed: '', startTime: null };
            // Reset platform configs
            this.s1c.sicKey = '';
            this.s1c.hardware = '';
            this.s1c.hardwareOptions = [];
            this.s1c.osVersion = 'R81.10';
            this.s1c.autoGenerateIp = true;
            this.s1c.ipAddress = '';
            this.s1c.vpnCommunity = '';
            this.s1c.policyName = '';
            this.lsm.mgmtServerIp = '';
            this.lsm.sicKey = '';
            this.lsm.securityProfile = '';
            this.lsm.provisioningProfile = '';
            this.lsm.domain = '';
            this.lsm.gatewayIpv4 = '';
            this.sms.mgmtServerIp = '';
            this.sms.sicKey = '';
            this.sms.gatewayIpv4 = '';
            this.sms.hardware = '';
            this.sms.version = '';
            this.sms.policyName = 'Standard';
            this.sms.enableAntiBot = true;
            this.sms.enableAntiVirus = true;
            this.sms.enableThreatEmulation = true;
            this.sms.vpnCommunity = '';
            this.sms.domain = '';
            this.sms.hardwareOptions = [];
            this.sms.versionOptions = [];
            // Reset SD-WAN
            this.sdwan.enabled = false;
            this.sdwan.profile = '';
            // Reset Gaia network
            Object.assign(this.gaiaNetwork, {
                mgmt_eth_ip_address_ipv4: '', mgmt_eth_subnet_mask_ipv4: '', default_gateway_ipv4: '',
                dns_server1: '', dns_server2: '', dns_server3: '',
                ntp1: 'ntp.checkpoint.com', ntp1_version: '4', ntp2: 'ntp2.checkpoint.com', ntp2_version: '4',
                timezone: 'UTC', admin_password: '', show_admin_password: false,
                proxy_server: '', proxy_port: 8080,
                config_ipv6: false, mgmt_eth_ip_address_ipv6: '', mgmt_eth_mask_length_ipv6: '', default_gateway_ipv6: '',
                upload_info: true, download_info: true
            });
        }
    },

    watch: {
        macAddress(v) {
            if (v && v.includes('-')) this.macAddress = v.replace(/-/g, ':');
        },
        gatewayType() {
            this.filterTemplates();
            // Reset management platform if SMP was selected and type switched to gaia
            if (this.managementPlatform === 'smp' && this.gatewayType !== 'spark') {
                this.managementPlatform = '';
            }
            // Reset claim when gateway type changes
            this.claimed = false;
            this.userScript = '';
        },
        selectedTemplateId() {
            // Reset claim when template changes
            this.claimed = false;
            this.userScript = '';
        },
        managementPlatform(v) {
            if (v === 'smart1-cloud') {
                this.fetchHardwareOptions();
                this.s1c.osVersion = this.gatewayType === 'spark' ? 'R81.10' : 'R82';
            }
            // SD-WAN is only available for Smart-1 Cloud and MDS/SMS — clear when unsupported
            if (v !== 'smart1-cloud' && v !== 'sms') {
                this.sdwan.enabled = false;
                this.sdwan.profile = '';
            }
            // Reset claim when management platform changes
            this.claimed = false;
            this.userScript = '';
        }
    }
}).mount('#app');
