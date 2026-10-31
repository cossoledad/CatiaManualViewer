"""Portable service configuration; credentials never enter exported data packages."""

import ipaddress
import re
import uuid

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "allow_subnets": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
    "auto_start": False,
    "require_token": False,
    "token_encrypted": "",
    "library_id": "",
}


def options(value):
    result = {**DEFAULTS, **value}
    host = result["host"]
    if host not in {"127.0.0.1", "0.0.0.0"}:
        address = ipaddress.IPv4Address(host)
        if not address.is_private or address.is_multicast or address.is_unspecified:
            raise ValueError("监听地址须为本机或局域网 IPv4 地址")
    if type(result["port"]) is not int or not 1024 <= result["port"] <= 65535:
        raise ValueError("端口须为 1024–65535")
    values = result["allow_subnets"]
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise ValueError("请配置 1–16 个允许网段")
    networks = []
    for value in values:
        net = ipaddress.IPv4Network(value, strict=False)
        if (
            net.prefixlen < 8
            or not net.network_address.is_private
            or not net.broadcast_address.is_private
        ):
            raise ValueError("允许网段须为局域网或本机 IPv4 网段")
        networks.append(str(net))
    if not any(ipaddress.IPv4Address("127.0.0.1") in ipaddress.IPv4Network(n) for n in networks):
        networks.insert(0, "127.0.0.0/8")
    result["allow_subnets"] = networks
    if type(result["auto_start"]) is not bool:
        raise ValueError("自动启动配置无效")
    if type(result["require_token"]) is not bool:
        raise ValueError("访问令牌配置无效")
    if result["library_id"]:
        uuid.UUID(result["library_id"])
    return {key: result[key] for key in DEFAULTS}


def valid_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("请重新生成 Agent 访问令牌")
    return token


def codex_config(address, port, *, require_token=False):
    ipaddress.IPv4Address(address)
    config = f'[mcp_servers.catia_manual]\nurl = "http://{address}:{port}/mcp"\n'
    if require_token:
        config += 'bearer_token_env_var = "CMV_MCP_TOKEN"\n'
    return config
