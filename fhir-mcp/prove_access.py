"""
Prove the access rule with your own login: list what you can see, then try to read a patient
from another department and show the rejection.

    python prove_access.py <url> "$TOKEN" [--other-mrn 20481733]

Which token depends on what you call (Azure CLI's app id 04b07795-8ddb-461a-bbee-02f9e1bf7b46
must be pre-authorised on the scope you request):

  Direct to the server (aud = mcp-api):
    TOKEN=$(az account get-access-token --scope "api://<mcp-api-app-id>/Tool.Write" --query accessToken -o tsv)
    python prove_access.py https://penn-fhir-mcp.<base-domain>/mcp "$TOKEN"

  Through the gateway (aud = agent-service; the gateway does the OBO exchange):
    TOKEN=$(az account get-access-token --scope "api://<agent-service-app-id>/access_as_user" --query accessToken -o tsv)
    python prove_access.py https://<control-plane>/api/llm/<tenant>/mcp/penn-fhir/server "$TOKEN"
    (the Agent Registry entry needs a mapping for 04b07795-... for this to resolve; or add
     --agent-token "$AGENT_TOKEN" to send x-tfy-agent-authorization like the mock agent does)

Default --other-mrn 20481733 is an Oncology patient. Pick any MRN from `python inventory.py`.
"""

import argparse
import asyncio
import json

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from rich.console import Console
from rich.table import Table

console = Console(highlight=False)


async def main(url: str, token: str, other_mrn: str, agent_token: str | None) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    if agent_token:
        headers["x-tfy-agent-authorization"] = f"Bearer {agent_token}"

    async with Client(StreamableHttpTransport(url, headers=headers)) as c:
        console.rule("[bold]1 · Who am I, according to the token the server received?")
        who = (await c.call_tool("whoami", {})).data
        console.print(json.dumps({k: who[k] for k in ("user", "user_name", "department", "actor_app", "aud")}, indent=2))

        console.rule("[bold]2 · list_my_patients: what I am allowed to see")
        mine = (await c.call_tool("list_my_patients", {})).data
        t = Table("mrn", "name", "department", "condition", title=f"{mine['count']} patient(s) for {mine['clinician']}")
        for p in mine["patients"]:
            t.add_row(p["mrn"], p["name"], p["department"], p["condition"])
        console.print(t)

        if mine["patients"]:
            own = mine["patients"][0]["mrn"]
            console.rule(f"[bold]3 · get_patient_records({own}): a patient in my department")
            b = (await c.call_tool("get_patient_records", {"mrn": own})).data
            console.print(f"[green]✔ allowed[/] · Bundle with {b['total']} resources: "
                          + ", ".join(e["resource"]["resourceType"] for e in b["entry"]))

        console.rule(f"[bold]4 · get_patient_records({other_mrn}): a patient in another department")
        try:
            await c.call_tool("get_patient_records", {"mrn": other_mrn})
            console.print("[bold red]Unexpected: the call succeeded. Check CLINICIAN_DEPARTMENTS.[/]")
        except ToolError as e:
            console.print(f"[bold red]✖ rejected[/] · {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("token")
    ap.add_argument("--other-mrn", default="20481733")
    ap.add_argument("--agent-token")
    a = ap.parse_args()
    asyncio.run(main(a.url, a.token, a.other_mrn, a.agent_token))
