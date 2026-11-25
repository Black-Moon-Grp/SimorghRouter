import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx


@dataclass
class ParsedOutbound:
    name: str
    protocol: str
    address: str
    port: int
    raw_link: str
    xray_outbound: dict[str, Any]


def _b64_decode(data: str) -> str:
    data = data.strip().replace("-", "+").replace("_", "/")
    padding = (-len(data)) % 4
    if padding:
        data += "=" * padding
    return base64.b64decode(data).decode("utf-8", errors="replace")


def _validate_host_port(host: str, port: int) -> None:
    if not host or any(c.isspace() for c in host) or len(host) > 255:
        raise ValueError(f"Invalid server address: {host!r}")
    if not (1 <= port <= 65535):
        raise ValueError(f"Invalid server port: {port}")


def _build_stream_settings(params: dict[str, str], default_sni: str = "") -> dict[str, Any]:
    network = params.get("type", "tcp").lower()
    if network in ("", "raw"):
        network = "tcp"
    security = params.get("security", "none").lower()

    stream: dict[str, Any] = {"network": network, "security": security}

    # Transport settings
    host_header = params.get("host", "")
    path = params.get("path", "/")
    if network == "ws":
        ws_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            ws_settings["headers"] = {"Host": host_header}
        stream["wsSettings"] = ws_settings
    elif network == "grpc":
        service_name = params.get("serviceName", "") or path.lstrip("/")
        stream["grpcSettings"] = {
            "serviceName": service_name,
            "multiMode": params.get("mode", "gun") == "multi",
        }
    elif network == "httpupgrade":
        hu_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            hu_settings["host"] = host_header
        stream["httpupgradeSettings"] = hu_settings
    elif network in ("xhttp", "splithttp"):
        xh_settings: dict[str, Any] = {"path": path or "/"}
        if host_header:
            xh_settings["host"] = host_header
        stream["xhttpSettings"] = xh_settings
    elif network == "tcp":
        header_type = params.get("headerType", "none").lower()
        if header_type == "http":
            stream["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "path": [path or "/"],
                        "headers": {"Host": [host_header]} if host_header else {},
                    },
                }
            }

    # Security settings
    sni = params.get("sni", "") or default_sni
    fp = params.get("fp", "")
    alpn_raw = params.get("alpn", "")
    alpn = [a.strip() for a in alpn_raw.split(",") if a.strip()] if alpn_raw else []
    allow_insecure = params.get("allowInsecure", "0") in ("1", "true", "True")

    if security == "tls":
        tls_settings: dict[str, Any] = {"allowInsecure": allow_insecure}
        if sni:
            tls_settings["serverName"] = sni
        if fp:
            tls_settings["fingerprint"] = fp
        if alpn:
            tls_settings["alpn"] = alpn
        stream["tlsSettings"] = tls_settings
    elif security == "reality":
        reality_settings: dict[str, Any] = {
            "show": False,
            "fingerprint": fp or "chrome",
            "serverName": sni,
            "publicKey": params.get("pbk", ""),
            "shortId": params.get("sid", ""),
            "spiderX": params.get("spx", "/"),
        }
        stream["realitySettings"] = reality_settings

    return stream


def parse_vless(link: str) -> ParsedOutbound:
    parsed = urlparse(link)
    if parsed.scheme.lower() != "vless":
        raise ValueError("Not a vless link")
    uuid = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    _validate_host_port(host, port)
    if not uuid:
        raise ValueError("VLESS link missing UUID")

    qs = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    name = unquote(parsed.fragment) if parsed.fragment else f"vless-{host}:{port}"
    flow = qs.get("flow", "")
    encryption = qs.get("encryption", "none") or "none"

    user_obj: dict[str, Any] = {"id": uuid, "encryption": encryption}
    if flow:
        user_obj["flow"] = flow

    outbound = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": port,
                    "users": [user_obj],
                }
            ]
        },
        "streamSettings": _build_stream_settings(qs, default_sni=host),
    }
    return ParsedOutbound(
        name=name[:120],
        protocol="vless",
        address=host,
        port=port,
        raw_link=link,
        xray_outbound=outbound,
    )


def parse_vmess(link: str) -> ParsedOutbound:
    if not link.lower().startswith("vmess://"):
        raise ValueError("Not a vmess link")
    payload_b64 = link[8:].strip()
    raw_json = _b64_decode(payload_b64)
    data = json.loads(raw_json)

    host = str(data.get("add", "")).strip()
    port = int(data.get("port", 443))
    _validate_host_port(host, port)
    uuid = str(data.get("id", "")).strip()
    if not uuid:
        raise ValueError("VMess link missing UUID")

    name = str(data.get("ps") or f"vmess-{host}:{port}")[:120]
    security = str(data.get("scy", "auto") or "auto")
    net = str(data.get("net", "tcp") or "tcp").lower()
    tls_val = str(data.get("tls", "") or "").lower()

    params = {
        "type": net,
        "security": "tls" if tls_val == "tls" else "none",
        "sni": str(data.get("sni", "") or data.get("host", "") or ""),
        "host": str(data.get("host", "") or ""),
        "path": str(data.get("path", "/") or "/"),
        "fp": str(data.get("fp", "") or ""),
        "alpn": str(data.get("alpn", "") or ""),
        "headerType": str(data.get("type", "none") or "none"),
    }

    outbound = {
        "tag": "proxy",
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": port,
                    "users": [
                        {
                            "id": uuid,
                            "alterId": int(data.get("aid", 0) or 0),
                            "security": security,
                        }
                    ],
                }
            ]
        },
        "streamSettings": _build_stream_settings(params, default_sni=host),
    }
    return ParsedOutbound(
        name=name,
        protocol="vmess",
        address=host,
        port=port,
        raw_link=link,
        xray_outbound=outbound,
    )


def parse_trojan(link: str) -> ParsedOutbound:
    parsed = urlparse(link)
    if parsed.scheme.lower() != "trojan":
        raise ValueError("Not a trojan link")
    password = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    _validate_host_port(host, port)
    if not password:
        raise ValueError("Trojan link missing password")

    qs = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    if "security" not in qs:
        qs["security"] = "tls"
    name = unquote(parsed.fragment) if parsed.fragment else f"trojan-{host}:{port}"

    outbound = {
        "tag": "proxy",
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": port,
                    "password": password,
                }
            ]
        },
        "streamSettings": _build_stream_settings(qs, default_sni=host),
    }
    return ParsedOutbound(
        name=name[:120],
        protocol="trojan",
        address=host,
        port=port,
        raw_link=link,
        xray_outbound=outbound,
    )


def parse_shadowsocks(link: str) -> ParsedOutbound:
    if not link.lower().startswith("ss://"):
        raise ValueError("Not a shadowsocks link")
    rest = link[5:]
    name = ""
    if "#" in rest:
        rest, frag = rest.split("#", 1)
        name = unquote(frag)

    # Remove optional plugin query string
    if "?" in rest:
        rest, _ = rest.split("?", 1)

    if "@" in rest:
        userinfo, hostport = rest.rsplit("@", 1)
        if ":" not in userinfo:
            userinfo = _b64_decode(userinfo)
        method, password = userinfo.split(":", 1)
    else:
        decoded = _b64_decode(rest)
        if "@" not in decoded:
            raise ValueError("Invalid Shadowsocks link format")
        userinfo, hostport = decoded.rsplit("@", 1)
        method, password = userinfo.split(":", 1)

    # Parse host:port (supports IPv6 [::1]:8388)
    m = re.match(r"^\[([^\]]+)\]:(\d+)$", hostport)
    if m:
        host, port = m.group(1), int(m.group(2))
    else:
        host, port_str = hostport.rsplit(":", 1)
        port = int(port_str)

    _validate_host_port(host, port)
    if not method or not password:
        raise ValueError("Shadowsocks link missing method or password")

    outbound = {
        "tag": "proxy",
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": port,
                    "method": method,
                    "password": password,
                }
            ]
        },
    }
    return ParsedOutbound(
        name=(name or f"ss-{host}:{port}")[:120],
        protocol="shadowsocks",
        address=host,
        port=port,
        raw_link=link,
        xray_outbound=outbound,
    )


def parse_link(link: str) -> ParsedOutbound:
    link = link.strip()
    lower = link.lower()
    if lower.startswith("vless://"):
        return parse_vless(link)
    if lower.startswith("vmess://"):
        return parse_vmess(link)
    if lower.startswith("trojan://"):
        return parse_trojan(link)
    if lower.startswith("ss://"):
        return parse_shadowsocks(link)
    raise ValueError("Unsupported protocol (expected vless://, vmess://, trojan://, or ss://)")


def parse_links_bulk(text: str) -> tuple[list[ParsedOutbound], list[str]]:
    parsed_list: list[ParsedOutbound] = []
    errors: list[str] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed_list.append(parse_link(line))
        except Exception as exc:
            errors.append(f"Line {idx}: {exc}")
    return parsed_list, errors


async def fetch_subscription(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Subscription URL must use http or https")
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url.strip(), headers={"User-Agent": "v2ray-smart-router/1.0"})
        resp.raise_for_status()
        body = resp.text.strip()

    # Subscription payloads are usually base64-encoded lists of links, or plain text
    if any(proto in body.lower() for proto in ("vless://", "vmess://", "trojan://", "ss://")):
        return body
    try:
        decoded = _b64_decode(body)
        if any(proto in decoded.lower() for proto in ("vless://", "vmess://", "trojan://", "ss://")):
            return decoded
    except Exception:
        pass
    return body


def generate_client_links(
    username: str,
    uuid: str,
    trojan_password: str,
    public_host: str,
    vless_tcp_port: int,
    vless_ws_port: int,
    vless_ws_path: str,
    trojan_port: int,
) -> dict[str, str]:
    host = public_host or "YOUR_VPS_IP"
    ws_path_encoded = quote(vless_ws_path if vless_ws_path.startswith("/") else f"/{vless_ws_path}")
    tag_tcp = quote(f"{username}-VLESS-TCP")
    tag_ws = quote(f"{username}-VLESS-WS")
    tag_trojan = quote(f"{username}-Trojan")

    vless_tcp = (
        f"vless://{uuid}@{host}:{vless_tcp_port}"
        f"?type=tcp&security=none&encryption=none#{tag_tcp}"
    )
    vless_ws = (
        f"vless://{uuid}@{host}:{vless_ws_port}"
        f"?type=ws&security=none&encryption=none&path={ws_path_encoded}#{tag_ws}"
    )
    trojan = (
        f"trojan://{quote(trojan_password)}@{host}:{trojan_port}"
        f"?type=tcp&security=none#{tag_trojan}"
    )
    return {
        "vless_tcp": vless_tcp,
        "vless_ws": vless_ws,
        "trojan": trojan,
    }
