"""Official MCP client over real TCP, LAN binding and viewer-owned process lifetime."""

import argparse
import asyncio
import json
import os
import runpy
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import tomlkit
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from catia_manual.config import load_settings
from catia_manual.core.agent import AgentKnowledge
from catia_manual.core.index import ManualIndex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--real", action="store_true", help="also read the configured installed manual")
parser.add_argument("--exe", type=Path, help="test the compiled portable EXE instead of Python")
parser.add_argument(
    "--require-token", action="store_true", help="verify optional bearer authentication"
)
parser.add_argument(
    "--wsl", action="store_true", help="also check token-free HTTP from the default WSL distro"
)
args = parser.parse_args()
if args.exe and not args.exe.is_file():
    parser.error("Portable EXE not found")
if args.wsl and (sys.platform != "win32" or args.require_token):
    parser.error("--wsl verifies token-free access from WSL on a Windows host")

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/agent-tcp" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder / "manual"
)
page = root / "online/shared_v17/coincide.htm"
page.write_text(
    page.read_text("utf-8") + "<h2>Details</h2><p>" + "Select the supporting face. " * 150 + "</p>",
    "utf-8",
)
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
config = folder / "config.toml"
config.write_text(
    tomlkit.dumps(
        {
            "documentation": {"root": str(root), "database": str(database)},
            "agent": {
                "host": "0.0.0.0",
                "port": port,
                "require_token": args.require_token,
                "token_encrypted": "" if args.require_token else "dpapi:v1:unreadable-on-this-host",
            },
        }
    ),
    "utf-8",
)
runtime = folder / "runtime.json"
lease = runtime.with_suffix(".lease")
token = secrets.token_urlsafe(32)
base = f"http://127.0.0.1:{port}"
report = {"checks": [], "failures": []}
process = None


def check(value, name):
    report["checks"].append({"name": name, "passed": bool(value)})
    if not value:
        report["failures"].append(name)
    print(("PASS " if value else "FAIL ") + name, flush=True)


def start(credential):
    lease.write_text("fixture-owner", "ascii")
    env = {
        **os.environ,
        "CMV_AGENT_INSTANCE": "fixture",
        "PYTHONUTF8": "1",
    }
    env.pop("CMV_AGENT_BOOT_TOKEN", None)
    if args.require_token:
        env["CMV_AGENT_BOOT_TOKEN"] = credential
    child = subprocess.Popen(
        [
            *(
                [str(args.exe.resolve())]
                if args.exe
                else [sys.executable, "-m", "catia_manual.entry"]
            ),
            "--agent-service",
            "--config",
            str(config),
            "--runtime",
            str(runtime),
        ],
        cwd=folder,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        lease.touch()
        if runtime.is_file():
            return child
        if child.poll() is not None:
            raise RuntimeError(
                "service startup failed: "
                + child.stderr.read().decode("utf-8", errors="replace")[-1800:]
            )
        time.sleep(0.05)
    child.kill()
    child.wait()
    raise TimeoutError("service startup timeout")


def stop(child):
    lease.unlink(missing_ok=True)
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        raise


async def protocol():
    def factory(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)

    async with (
        streamablehttp_client(
            base + "/mcp",
            headers={"Authorization": "Bearer " + token} if args.require_token else {},
            httpx_client_factory=factory,
        ) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        check(
            {t.name for t in tools}
            == {"describe_library", "search", "fetch", "browse", "resolve_source"}
            and all(t.annotations.readOnlyHint for t in tools),
            "official MCP client initializes and discovers five read-only tools",
        )
        description = await session.call_tool("describe_library")
        check(
            description.structuredContent["index_ready"],
            "MCP returns the pinned library and index readiness",
        )
        for query, expected in (
            ("相合约束", "coincide.htm"),
            ("偏移约束", "offset.htm"),
            ("管理约束", "overview.htm"),
            ("Managing Constraints", "overview.htm"),
            ("Coincidence Constraint", "coincide.htm"),
        ):
            result = await session.call_tool("search", {"query": query, "manual": "asmug_C2"})
            hit = result.structuredContent["results"][0]
            check(
                hit["source"]["document"]["path"].endswith(expected),
                "TCP bilingual scoped search: " + query,
            )
        target = (await session.call_tool("search", {"query": "相合约束"})).structuredContent[
            "results"
        ][0]
        cursor, collected = None, []
        while True:
            result = await session.call_tool(
                "fetch", {"id": target["article_id"], "cursor": cursor, "max_chars": 1000}
            )
            value = result.structuredContent
            collected.extend(b["text"] for b in value["blocks"])
            if not value["has_more"]:
                break
            cursor = value["next_cursor"]
        check(
            "".join(collected).count("Select the supporting face.") == 150,
            "TCP pagination returns every character of a long source block",
        )
        rejected = await session.call_tool("fetch", {"id": "../../config.toml"})
        check(
            rejected.isError and rejected.structuredContent["error"]["code"] == "INVALID_REFERENCE",
            "invalid paths are domain errors and do not expose files",
        )
        source = target["source"]
        resolved = await session.call_tool("resolve_source", {"source": source})
        check(
            not resolved.isError and resolved.structuredContent["id"],
            "TCP saved source can be resolved into a fresh read handle",
        )


try:
    process = start(token)
    with httpx.Client(trust_env=False, timeout=3) as client:
        check(
            client.get(base + "/health").status_code == (401 if args.require_token else 200),
            "token mode rejects missing credentials"
            if args.require_token
            else "default LAN service accepts clients without any token or environment variable",
        )
        auth = {"Authorization": "Bearer " + token} if args.require_token else {}
        check(
            client.get(base + "/health", headers=auth).json()["readonly"],
            "health check succeeds under the selected access mode",
        )
        check(
            client.get(
                base + "/health", headers={**auth, "Origin": "http://attacker.invalid"}
            ).status_code
            == 403,
            "real TCP rejects foreign browser origins",
        )
        addresses = sorted(
            {
                i[4][0]
                for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                if i[4][0].startswith(("10.", "192.168.", "172."))
            }
        )
        if addresses:
            address = addresses[0]
            check(
                client.get(f"http://{address}:{port}/health", headers=auth).status_code == 200,
                "service is reachable through this computer's actual LAN interface",
            )
            report["lan_interface_checked"] = address
        if args.wsl:
            # Linux needs only its standard library, no MCP package or credentials.
            code = """
import json, sys, urllib.request
client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = None
for address in sys.argv[2:]:
    try:
        candidate = 'http://' + address + ':' + sys.argv[1]
        with client.open(candidate + '/health', timeout=2) as response:
            health = json.load(response)
        if health['readonly']:
            base = candidate
            break
    except OSError:
        pass
assert base, 'WSL could not reach the Windows listener'
def rpc(method, params):
    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
    request = urllib.request.Request(base + '/mcp', data=body, headers={
        'Content-Type':'application/json', 'Accept':'application/json, text/event-stream'})
    with client.open(request, timeout=5) as response:
        return json.load(response)['result']
initialized = rpc('initialize', {'protocolVersion':'2025-11-25',
    'capabilities':{},'clientInfo':{'name':'cmv-wsl-check','version':'1'}})
assert initialized['serverInfo']['name'] == 'Catia Manual Viewer'
result = rpc('tools/call', {'name':'search',
    'arguments':{'query':'Coincidence Constraint','manual':'asmug_C2','limit':1}})
hit = result['structuredContent']['results'][0]
page = rpc('tools/call', {'name':'fetch','arguments':{'id':hit['article_id']}})
assert not page.get('isError', False) and page['structuredContent']['blocks']
print(json.dumps({'url':base, 'path':page['structuredContent']['path']}))
"""
            result = subprocess.run(
                ["wsl", "--exec", "python3", "-c", code, str(port), *addresses, "127.0.0.1"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
            )
            lease.touch()
            if result.returncode:
                raise RuntimeError("WSL check failed: " + result.stderr[-1500:])
            report["wsl"] = json.loads(result.stdout)
            check(True, "WSL initializes MCP, searches and reads Windows sources without a token")
    before = database.read_bytes()
    asyncio.run(asyncio.wait_for(protocol(), timeout=25))
    check(
        database.read_bytes() == before,
        "complete MCP workflow leaves the database byte-for-byte unchanged",
    )
    stop(process)
    check(
        process.returncode == 0 and not runtime.exists(),
        "removing the owner lease gracefully stops the backend and cleans readiness state",
    )
    if args.require_token:
        old_token, token = token, secrets.token_urlsafe(32)
        process = start(token)
        with httpx.Client(trust_env=False, timeout=3) as client:
            check(
                client.get(
                    base + "/health", headers={"Authorization": "Bearer " + old_token}
                ).status_code
                == 401
                and client.get(
                    base + "/health", headers={"Authorization": "Bearer " + token}
                ).status_code
                == 200,
                "restarting with a rotated token revokes the old connection credential",
            )
        stop(process)
    # Read actual installed documentation without listeners, models, API calls or writes.
    if args.real:
        service = AgentKnowledge(load_settings())
        report["real"] = []
        for query in (
            "相合约束",
            "偏移约束",
            "管理约束",
            "Managing Constraints",
            "Coincidence Constraint",
        ):
            result = service.search(query, manual="asmug_C2", limit=3)
            hit = result["results"][0]
            content = service.fetch(hit["id"])
            check(bool(content["blocks"]), "installed manual search and source reading: " + query)
            report["real"].append(
                {"query": query, "title": hit["title"], "path": hit["source"]["document"]["path"]}
            )
except Exception as exc:
    report["failures"].append(str(exc))
    print(type(exc).__name__ + ": " + str(exc), flush=True)
finally:
    if process and process.poll() is None:
        lease.unlink(missing_ok=True)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    output = project / "data"
    output.mkdir(exist_ok=True)
    report["passed"] = not report["failures"]
    report["portable_entry"] = bool(args.exe)
    report["require_token"] = args.require_token
    name = "agent-portable-tcp" if args.exe else "agent-tcp"
    (output / (name + ("-token" if args.require_token else "") + ".json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
raise SystemExit(0 if report["passed"] else 1)
