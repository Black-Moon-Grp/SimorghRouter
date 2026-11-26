function app() {
  return {
    token: localStorage.getItem('v2ray_token') || '',
    tab: 'outbounds',
    loading: false,
    toast: { show: false, message: '', type: 'success' },

    loginForm: { username: 'admin', password: '' },
    overview: {
      xray_main_running: false,
      xray_outbound_running: false,
      active_outbound: null,
      total_outbounds: 0,
      alive_outbounds: 0,
      total_clients: 0,
      active_clients: 0,
      total_traffic_bytes: 0,
      speedtest_interval_min: 30,
      run_speed_dl_on_schedule: false,
      public_host: '',
      vless_tcp_port: 443,
      vless_ws_port: 8443,
      vless_ws_path: '/ws',
      trojan_port: 2083,
      last_speedtest_at: null,
      last_watchdog_at: null,
      is_testing_now: false,
    },

    outbounds: [],
    clients: [],
    liveUsers: [],
    activityLogs: [],
    logFilter: { username: '', search: '' },
    rules: [],
    geo: {
      geoip_exists: false,
      geoip_size_mb: 0,
      geoip_updated_at: null,
      geosite_exists: false,
      geosite_size_mb: 0,
      geosite_updated_at: null,
      geoip_url: '',
      geosite_url: '',
      weekly_auto_update: true,
    },

    importForm: { subscription_url: '', links_text: '' },
    clientForm: { username: '', quota_gb: 0, expires_in_days: 30 },
    ruleForm: { rule_type: 'domain', value: '', action: 'proxy' },
    settingsForm: {
      speedtest_interval_min: 30,
      run_speed_dl_on_schedule: false,
      public_host: '',
      vless_tcp_port: 443,
      vless_ws_port: 8443,
      vless_ws_path: '/ws',
      trojan_port: 2083,
    },
    pwForm: { current_password: '', new_password: '' },
    updatingApp: false,
    updateInfo: { local_commit: '', remote_commit: '', remote_message: '', update_available: false },
    qrModal: { open: false, clientId: null, username: '', proto: 'vless_tcp', links: {}, qrUrl: '' },

    init() {
      if (this.token) {
        this.refreshAll();
      }
      setInterval(() => {
        if (this.token) {
          this.loadOverview();
          this.loadOutbounds();
          this.loadClients();
        }
      }, 10000);
      setInterval(() => {
        if (this.token && this.tab === 'logs') {
          this.loadActivity();
        }
      }, 5000);
    },

    notify(message, type = 'success') {
      this.toast = { show: true, message, type };
      setTimeout(() => { this.toast.show = false; }, 3500);
    },

    async api(path, opts = {}) {
      const headers = opts.headers || {};
      if (this.token) headers['Authorization'] = `Bearer ${this.token}`;
      if (opts.body && !(opts.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
      }
      const res = await fetch(path, { ...opts, headers });
      if (res.status === 401) {
        this.logout();
        throw new Error('Session expired');
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        let msg = 'Request failed';
        if (Array.isArray(err.detail)) {
          msg = err.detail.map(e => `${(e.loc || []).slice(-1)[0] || 'field'}: ${e.msg}`).join(', ');
        } else if (typeof err.detail === 'string') {
          msg = err.detail;
        } else if (err.detail) {
          msg = JSON.stringify(err.detail);
        }
        throw new Error(msg);
      }
      return res;
    },

    async login() {
      this.loading = true;
      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.loginForm),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Login failed');
        this.token = data.access_token;
        localStorage.setItem('v2ray_token', this.token);
        this.loginForm.password = '';
        await this.refreshAll();
      } catch (e) {
        this.notify(e.message, 'error');
      } finally {
        this.loading = false;
      }
    },

    logout() {
      this.token = '';
      localStorage.removeItem('v2ray_token');
    },

    async refreshAll() {
      await Promise.all([
        this.loadOverview(true),
        this.loadOutbounds(),
        this.loadClients(),
        this.loadRules(),
        this.loadGeo(),
        this.checkUpdate(),
      ]);
    },

    async loadOverview(syncSettingsForm = false) {
      try {
        const data = await (await this.api('/api/system/overview')).json();
        this.overview = data;
        if (syncSettingsForm) {
          this.settingsForm = {
            speedtest_interval_min: data.speedtest_interval_min,
            run_speed_dl_on_schedule: data.run_speed_dl_on_schedule,
            public_host: data.public_host,
            vless_tcp_port: data.vless_tcp_port,
            vless_ws_port: data.vless_ws_port,
            vless_ws_path: data.vless_ws_path,
            trojan_port: data.trojan_port,
          };
        }
      } catch {}
    },

    async loadOutbounds() {
      try { this.outbounds = await (await this.api('/api/outbounds')).json(); } catch {}
    },

    async loadClients() {
      try { this.clients = await (await this.api('/api/clients')).json(); } catch {}
    },

    async loadActivity() {
      await Promise.all([this.loadLiveUsers(), this.loadActivityLogs()]);
    },

    async loadLiveUsers() {
      try { this.liveUsers = await (await this.api('/api/clients/live-status')).json(); } catch {}
    },

    async loadActivityLogs() {
      try {
        const params = new URLSearchParams();
        if (this.logFilter.username) params.set('username', this.logFilter.username);
        if (this.logFilter.search) params.set('search', this.logFilter.search);
        this.activityLogs = await (await this.api(`/api/clients/activity-logs?${params.toString()}`)).json();
      } catch {}
    },

    async loadRules() {
      try { this.rules = await (await this.api('/api/routing')).json(); } catch {}
    },

    async loadGeo() {
      try { this.geo = await (await this.api('/api/geo/status')).json(); } catch {}
    },

    async importOutbounds() {
      try {
        const res = await (await this.api('/api/outbounds/import', {
          method: 'POST',
          body: JSON.stringify(this.importForm),
        })).json();
        this.importForm = { subscription_url: '', links_text: '' };
        this.notify(`Imported ${res.imported}, skipped ${res.skipped_duplicates} duplicates`);
        await this.loadOutbounds();
        await this.loadOverview();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async triggerSpeedTest(runSpeedDl) {
      try {
        await this.api('/api/outbounds/test', {
          method: 'POST',
          body: JSON.stringify({ run_speed_dl: runSpeedDl }),
        });
        this.overview.is_testing_now = true;
        this.notify('Speed test started in background');
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async activateOutbound(id) {
      try {
        await this.api(`/api/outbounds/${id}/activate`, { method: 'POST' });
        this.notify('Switched active outbound');
        await this.refreshAll();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async toggleOutbound(id) {
      try {
        await this.api(`/api/outbounds/${id}/toggle`, { method: 'PATCH' });
        await this.loadOutbounds();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async deleteOutbound(id) {
      try {
        await this.api(`/api/outbounds/${id}`, { method: 'DELETE' });
        await this.loadOutbounds();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async purgeDeadOutbounds() {
      try {
        const res = await (await this.api('/api/outbounds/dead', { method: 'DELETE' })).json();
        this.notify(`Purged ${res.deleted} dead outbounds`);
        await this.loadOutbounds();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async createClient() {
      try {
        const payload = {
          username: (this.clientForm.username || '').trim(),
          quota_gb: Number(this.clientForm.quota_gb) || 0,
          expires_in_days: this.clientForm.expires_in_days ? Number(this.clientForm.expires_in_days) : null,
        };
        await this.api('/api/clients', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        this.clientForm.username = '';
        this.notify('Client created & synced to Xray');
        await this.loadClients();
        await this.loadOverview();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async toggleClient(c) {
      try {
        await this.api(`/api/clients/${c.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: !c.enabled }),
        });
        await this.loadClients();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async resetClientTraffic(id) {
      try {
        await this.api(`/api/clients/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({ reset_traffic: true, enabled: true }),
        });
        this.notify('Client traffic reset');
        await this.loadClients();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async deleteClient(id) {
      try {
        await this.api(`/api/clients/${id}`, { method: 'DELETE' });
        this.notify('Client deleted');
        await this.loadClients();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async openClientLinks(id) {
      try {
        const links = await (await this.api(`/api/clients/${id}/links`)).json();
        this.qrModal = {
          open: true,
          clientId: id,
          username: links.username,
          proto: 'sub',
          links,
          qrUrl: '',
        };
        await this.loadQrImage();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async selectQrProto(proto) {
      this.qrModal.proto = proto;
      await this.loadQrImage();
    },

    async loadQrImage() {
      if (this.qrModal.qrUrl) URL.revokeObjectURL(this.qrModal.qrUrl);
      const res = await this.api(`/api/clients/${this.qrModal.clientId}/qr?proto=${this.qrModal.proto}`);
      const blob = await res.blob();
      this.qrModal.qrUrl = URL.createObjectURL(blob);
    },

    copyLink(text) {
      navigator.clipboard.writeText(text || '');
      this.notify('Copied to clipboard');
    },

    async createRule() {
      try {
        await this.api('/api/routing', {
          method: 'POST',
          body: JSON.stringify(this.ruleForm),
        });
        this.ruleForm.value = '';
        this.notify('Rule added & applied');
        await this.loadRules();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async deleteRule(id) {
      try {
        await this.api(`/api/routing/${id}`, { method: 'DELETE' });
        this.notify('Rule removed & applied');
        await this.loadRules();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async updateGeo() {
      this.loading = true;
      try {
        await this.api('/api/geo/update', {
          method: 'POST',
          body: JSON.stringify({
            geoip_url: this.geo.geoip_url,
            geosite_url: this.geo.geosite_url,
            weekly_auto_update: this.geo.weekly_auto_update,
          }),
        });
        this.notify('GeoIP & Geosite updated');
        await this.loadGeo();
      } catch (e) { this.notify(e.message, 'error'); }
      finally { this.loading = false; }
    },

    async uploadGeo(event, targetName) {
      const file = event.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      try {
        await this.api(`/api/geo/upload/${targetName}`, { method: 'POST', body: fd });
        this.notify(`Uploaded ${targetName}`);
        await this.loadGeo();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async saveSettings() {
      try {
        await this.api('/api/system/settings', {
          method: 'PATCH',
          body: JSON.stringify(this.settingsForm),
        });
        this.notify('Settings saved');
        await this.loadOverview();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async restartXray() {
      try {
        await this.api('/api/system/restart-xray', { method: 'POST' });
        this.notify('Xray restarted');
        await this.loadOverview();
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async changePassword() {
      try {
        await this.api('/api/auth/change-password', {
          method: 'POST',
          body: JSON.stringify(this.pwForm),
        });
        this.pwForm = { current_password: '', new_password: '' };
        this.notify('Password updated');
      } catch (e) { this.notify(e.message, 'error'); }
    },

    async checkUpdate() {
      try {
        this.updateInfo = await (await this.api('/api/system/update-check')).json();
      } catch {}
    },

    async selfUpdate() {
      this.updatingApp = true;
      try {
        const res = await (await this.api('/api/system/self-update', { method: 'POST' })).json();
        this.notify(`Updated to ${res.commit}! Restarting panel...`);
        setTimeout(() => { window.location.reload(); }, 5000);
      } catch (e) {
        this.notify(e.message, 'error');
        this.updatingApp = false;
      }
    },

    formatBytes(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let i = 0;
      let val = bytes;
      while (val >= 1024 && i < units.length - 1) {
        val /= 1024;
        i++;
      }
      return `${val.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
    },

    formatTime(iso) {
      if (!iso) return 'Never';
      try { return new Date(iso).toLocaleString(); } catch { return iso; }
    },
  };
}
