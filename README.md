<p align="center">
  <img src="app/static/logo.jpg" alt="SimorghRouter Logo" width="120" />
</p>

# SimorghRouter

Production-ready Linux (Ubuntu/Debian) smart Xray auto-router and multi-user gateway powered by **Xray-core** and **FastAPI**.

## Key Features
- **Automated Speed Tester & Smart Failover**: Bulk import `vless://`, `vmess://`, `trojan://`, and `ss://` links or subscription URLs. Concurrent worker pool tests configs on isolated local SOCKS5 ports against `https://www.gstatic.com/generate_204`.
- **Zero-Drop Outbound Hot-Swap**: Uses a local TCP bridge (`127.0.0.1:10808`) and alternating blue-green outbound Xray processes (`10809` / `10810`) so switching to the fastest outbound or failing over via the 1-minute watchdog **never restarts** the main Xray core or drops active Iranian/direct connections.
- **Iran Direct Bypass & Custom Routing**: Routes `geosite:category-ir`, `domain:.ir`, `geoip:ir`, and `geoip:private` directly through `freedom`, with UI management for custom `direct` / `proxy` / `block` rules and `geoip.dat` / `geosite.dat` updates.
- **Multi-User Inbound & Strict Quota Enforcement**: Supports VLESS TCP, VLESS WS, and Trojan. Polls Xray's localhost-only gRPC `StatsService` every 12 seconds and immediately revokes expired or over-quota users via `HandlerService` (`RemoveUserOperation`) without interrupting other clients.

## Quick Deployment (Ubuntu / Debian)
```bash
sudo bash deploy/install.sh
```
Check status:
```bash
systemctl status v2ray-router
journalctl -u v2ray-router -f
```

## Docker Deployment
```bash
cd deploy
docker compose up -d --build
```
