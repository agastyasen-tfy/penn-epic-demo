"""
Offline test: runs a fake JWKS endpoint + the server with a throwaway RSA key, then checks
  1. no token -> 401, wrong scope -> 401
  2. mapped clinician (cardiology) sees only cardiology patients
  3. same clinician is rejected for an oncology MRN
  4. unmapped user and app-only token are rejected
The server's terminal view is written to server_output.log (pass --show to print it).

    pip install -r requirements.txt PyJWT cryptography
    python test_local.py [--show]
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import httpx2 as httpx  # fastmcp >= 4 depends on httpx2
except ImportError:
    import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp import Client
from fastmcp.exceptions import ToolError

TENANT = str(uuid.uuid4())
MCP_API = str(uuid.uuid4())
JWKS_PORT, MCP_PORT = 8790, 8791
CLINICIAN = "agastya.sen@truefoundry.com"

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": "test-kid", "use": "sig", "alg": "RS256"}


class JWKS(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"keys": [jwk]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def mint(scp: str | None = "Tool.Write", upn: str | None = CLINICIAN, name: str = "Agastya Sen"):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": MCP_API,
        "iat": now, "nbf": now, "exp": now + 600, "azp": "agent-service-app-id", "tid": TENANT,
    }
    if upn:
        claims |= {"oid": str(uuid.uuid5(uuid.NAMESPACE_DNS, upn)), "preferred_username": upn, "name": name}
    if scp:
        claims["scp"] = scp
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-kid"})


async def expect_denied(c: Client, tool: str, args: dict, label: str):
    try:
        await c.call_tool(tool, args)
    except ToolError as e:
        assert "denied" in str(e).lower(), e
        print(f"{label:<34}-> rejected: {e}")
        return
    raise AssertionError(f"{label}: expected denial")


async def main():
    threading.Thread(target=HTTPServer(("127.0.0.1", JWKS_PORT), JWKS).serve_forever, daemon=True).start()
    env = os.environ | {
        "ENTRA_TENANT_ID": TENANT, "MCP_API_APP_ID": MCP_API, "PORT": str(MCP_PORT),
        "FASTMCP_TEST_JWKS": f"http://127.0.0.1:{JWKS_PORT}/keys",
        "CLINICIAN_DEPARTMENTS": json.dumps({CLINICIAN: "cardiology"}),
        "FORCE_COLOR": os.environ.get("FORCE_COLOR", ""), "CONSOLE_WIDTH": "150",
    }
    out = open("server_output.log", "w")
    proc = subprocess.Popen([sys.executable, "server.py"], env=env, stdout=out, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{MCP_PORT}/mcp"
    try:
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{MCP_PORT}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)

        hdr = {"Accept": "application/json, text/event-stream"}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert httpx.post(url, json=body, headers=hdr).status_code == 401
        print("no token                          -> 401  OK")
        assert httpx.post(url, json=body, headers=hdr | {"Authorization": f"Bearer {mint('Other.Scope')}"}).status_code == 401
        print("wrong scope                       -> 401  OK")

        async with Client(url, auth=mint()) as c:
            tools = sorted(t.name for t in await c.list_tools())
            assert tools == ["get_patient", "get_patient_records", "list_my_patients", "whoami"], tools
            who = (await c.call_tool("whoami", {})).data
            assert who["user"] == CLINICIAN and who["department"] == "Cardiology", who
            print(f"whoami                            -> {who['user']} · {who['department']}")

            mine = (await c.call_tool("list_my_patients", {})).data
            depts = {p["department"] for p in mine["patients"]}
            assert mine["count"] == 3 and depts == {"Cardiology"}, mine
            print(f"list_my_patients                  -> {mine['count']} rows, departments {sorted(depts)}")

            b = (await c.call_tool("get_patient_records", {"mrn": "10519022"})).data
            assert b["resourceType"] == "Bundle" and b["total"] == 4
            print("get_patient_records(own MRN)      -> Bundle, 4 resources  OK")

            await expect_denied(c, "get_patient_records", {"mrn": "20481733"}, "get_patient_records(oncology MRN)")
            await expect_denied(c, "get_patient", {"mrn": "40458812"}, "get_patient(pediatrics MRN)")
            try:
                await c.call_tool("get_patient", {"mrn": "99999999"})
                raise AssertionError("expected not found")
            except ToolError as e:
                print(f"get_patient(unknown MRN)          -> {e}")

        async with Client(url, auth=mint(upn="someone.else@truefoundry.com", name="Someone Else")) as c:
            await expect_denied(c, "list_my_patients", {}, "unmapped user, list_my_patients")
        async with Client(url, auth=mint(upn=None)) as c:
            await expect_denied(c, "list_my_patients", {}, "app-only token, list_my_patients")
        print("\nALL CHECKS PASSED  (server terminal view: server_output.log)")
    finally:
        proc.terminate()
        proc.wait()
        out.close()
        err = proc.stderr.read().decode()
        if "Traceback" in err:
            print(err)
        if "--show" in sys.argv:
            print(open("server_output.log").read())


if __name__ == "__main__":
    asyncio.run(main())
