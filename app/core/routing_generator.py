import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import ClientAccount, RoutingRule


def generate_outbound_runner_config(
    listen_port: int,
    outbound_dict: dict[str, Any],
) -> dict[str, Any]:
    """Generates a lightweight Xray config for a single outbound behind a local SOCKS5 inbound."""
    outbound = dict(outbound_dict)
    outbound["tag"] = "proxy"
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": listen_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            }
        ],
        "outbounds": [outbound],
    }


def generate_main_xray_config(
    clients: list[ClientAccount],
    custom_rules: list[RoutingRule],
    vless_tcp_port: int,
    vless_ws_port: int,
    vless_ws_path: str,
    trojan_port: int,
) -> dict[str, Any]:
    """
    Generates the Main Xray-core configuration:
    - Localhost-only gRPC API (HandlerService + StatsService)
    - Multi-user VLESS TCP, VLESS WS, and Trojan inbounds
    - Proxy outbound pointing to the local zero-drop bridge (127.0.0.1:OUTBOUND_BRIDGE_PORT)
    - Freedom (direct) & Blackhole (block) outbounds
    - Routing with Iran bypass (geosite:category-ir, domain:.ir, geoip:ir, geoip:private) + custom rules
    """
    grpc_host, grpc_port_str = settings.XRAY_GRPC_ADDR.rsplit(":", 1)
    grpc_port = int(grpc_port_str)

    vless_clients = [
        {"id": c.uuid, "email": c.email, "level": 0}
        for c in clients
        if c.enabled
    ]
    trojan_clients = [
        {"password": c.trojan_password, "email": c.email, "level": 0}
        for c in clients
        if c.enabled
    ]

    ws_path = vless_ws_path if vless_ws_path.startswith("/") else f"/{vless_ws_path}"

    # Check if geoip.dat and geosite.dat exist so Xray never fails to boot if missing
    geoip_exists = (settings.assets_path / "geoip.dat").is_file()
    geosite_exists = (settings.assets_path / "geosite.dat").is_file()

    routing_rules: list[dict[str, Any]] = [
        {
            "type": "field",
            "inboundTag": ["api"],
            "outboundTag": "api",
        }
    ]

    # Group custom rules by (action, rule_type)
    for action in ("block", "direct", "proxy"):
        domains = [
            r.value for r in custom_rules
            if r.enabled and r.action == action and r.rule_type == "domain"
            and (geosite_exists or not r.value.startswith("geosite:"))
        ]
        ips = [
            r.value for r in custom_rules
            if r.enabled and r.action == action and r.rule_type == "ip"
            and (geoip_exists or not r.value.startswith("geoip:"))
        ]
        if domains:
            routing_rules.append({
                "type": "field",
                "domain": domains,
                "outboundTag": action,
            })
        if ips:
            routing_rules.append({
                "type": "field",
                "ip": ips,
                "outboundTag": action,
            })

    # Mandatory Iran & Private IP direct bypass
    bypass_ips = ["geoip:private"] if geoip_exists else [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7"
    ]
    if geoip_exists:
        bypass_ips.append("geoip:ir")

    bypass_domains = ["domain:.ir"]
    if geosite_exists:
        bypass_domains.insert(0, "geosite:category-ir")

    routing_rules.append({
        "type": "field",
        "ip": bypass_ips,
        "outboundTag": "direct",
    })
    routing_rules.append({
        "type": "field",
        "domain": bypass_domains,
        "outboundTag": "direct",
    })

    # Default catch-all -> proxy
    routing_rules.append({
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": "proxy",
    })

    access_log_path = (settings.data_path / "xray_access.log").as_posix()
    error_log_path = (settings.data_path / "xray_error.log").as_posix()

    return {
        "log": {
            "loglevel": "info",
            "access": access_log_path,
            "error": error_log_path,
        },
        "stats": {},
        "api": {
            "tag": "api",
            "services": ["HandlerService", "StatsService"],
        },
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
            },
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": grpc_host,
                "port": grpc_port,
                "protocol": "dokodemo-door",
                "settings": {"address": grpc_host},
            },
            {
                "tag": "in-vless-tcp",
                "listen": "0.0.0.0",
                "port": vless_tcp_port,
                "protocol": "vless",
                "settings": {
                    "clients": vless_clients,
                    "decryption": "none",
                },
                "streamSettings": {"network": "tcp", "security": "none"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            },
            {
                "tag": "in-vless-ws",
                "listen": "0.0.0.0",
                "port": vless_ws_port,
                "protocol": "vless",
                "settings": {
                    "clients": vless_clients,
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "none",
                    "wsSettings": {"path": ws_path},
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            },
            {
                "tag": "in-trojan",
                "listen": "0.0.0.0",
                "port": trojan_port,
                "protocol": "trojan",
                "settings": {"clients": trojan_clients},
                "streamSettings": {"network": "tcp", "security": "none"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            },
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "socks",
                "settings": {
                    "servers": [
                        {
                            "address": "127.0.0.1",
                            "port": settings.OUTBOUND_BRIDGE_PORT,
                        }
                    ]
                },
            },
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {"domainStrategy": "UseIPv4"},
            },
            {
                "tag": "block",
                "protocol": "blackhole",
                "settings": {"response": {"type": "http"}},
            },
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": routing_rules,
        },
    }


def write_json_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp_path.replace(path)
