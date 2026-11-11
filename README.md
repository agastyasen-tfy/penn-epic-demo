# Penn Medicine demo: clinician → agent → mock Epic FHIR, with department access

Built from `netp_agent` (the Entra OBO demo), which is left untouched. Identity flows exactly as
there: you sign in to the agent, the agent sends your token and its own token to the TrueFoundry
gateway, the gateway does the on-behalf-of exchange, and the MCP server receives a token that still
names you. The new part is the MCP server: a mock Epic FHIR source that reads your
`preferred_username`, looks up your department, and returns only that department's patients.

| Folder      | What it is                                                                                     | Port |
| ----------- | ---------------------------------------------------------------------------------------------- | ---- |
| `fhir-mcp/` | Mock Epic FHIR MCP server. Same Entra JWT checks as `mcp-obo-demo` + department access rule.   | 8000 |
| `agent/`    | Copy of `mock-agent` with a clinical system prompt and name. Code otherwise unchanged.         | 8080 |

The data is synthetic: 16 patients across Cardiology, Oncology, Neurology, Pediatrics and
Emergency Medicine (`fhir-mcp/data.py`).

## What changes vs. the NetApp demo

**Nothing in Entra.** The new server validates the same token as `mcp-obo-demo` (aud = `mcp-api`,
scope `Tool.Write`), so the same three app registrations and the same Identity Provider work.

**In TrueFoundry, only additions:**
1. Deploy `fhir-mcp` as a new Service (`penn-fhir-mcp`).
2. MCP Gateway → Add Server → a new remote server `penn-fhir`, URL `https://penn-fhir-mcp.<base-domain>/mcp`,
   and **the same Auth Data as `obo-demo`** (OAuth2 · Microsoft Entra · JWT Bearer · agent-service
   client ID + secret FQN · scope `api://<mcp-api id>/Tool.Write`).
3. `penn-fhir` → Collaborators → Agents → `mock-agent` as **MCP Server User** (same agent identity).
4. Deploy `agent` as a new Service (`penn-clinical-agent`) with `MCP_GATEWAY_URL` = the `penn-fhir`
   gateway URL. All other env values are the same as the existing mock agent.
5. Optional: a PII guardrail on `mcp_tool_post_invoke` for `penn-fhir`, to show PHI masking in traces.

## Deploy

```bash
# MCP server
cd fhir-mcp
export WORKSPACE_FQN=<cluster>:<workspace>
export HOST=penn-fhir-mcp.<cluster-base-domain>
export ENTRA_TENANT_ID=<tenant-id> MCP_API_APP_ID=<mcp-api client id>   # same as mcp-obo-demo
export CLINICIAN_DEPARTMENTS='{"agastya.sen@truefoundry.com": "cardiology"}'
python deploy.py

# agent
cd ../agent
export HOST=penn-clinical-agent.<cluster-base-domain>
export MCP_GATEWAY_URL=<penn-fhir gateway URL from How To Use>
# ...plus the same ENTRA_TENANT_ID, AGENT_CLIENT_ID, GATEWAY_AUDIENCE_APP_ID,
#    AGENT_CLIENT_SECRET_FQN, LLM_GATEWAY_BASE_URL, LLM_MODEL as the existing mock agent
python deploy.py
```

`CLINICIAN_DEPARTMENTS` maps sign-in emails to one of `cardiology`, `oncology`, `neurology`,
`pediatrics`, `emergency`. Anyone not listed gets nothing. To switch your department for a second
run, change the value and redeploy (or edit the env var on the Service).

## Where the terminal view shows up

Every tool call prints a panel to the server's stdout:

```
╭──────────── MCP tool call · get_patient_records(mrn=20481733) ────────────╮
│ 1  Incoming token from  Agastya Sen  <agastya.sen@truefoundry.com>        │
│    oid 4d28ec9a… · acting app (azp) … · aud … ✓ · scp Tool.Write          │
│ 2  Checking clinician → department mapping  agastya.sen@…  →  CARDIOLOGY  │
│ 3  Access rule  patient's department must equal caller's department       │
│ 4  Result  ✖ ACCESS DENIED  MRN 20481733 belongs to ONCOLOGY; …           │
╰───────────────────────────────────────────────────────────────────────────╯
```

Allowed calls also print the rows returned, and `list_my_patients` says how many patients in the
source were withheld and from which departments.

- **Deployed:** TrueFoundry → the `penn-fhir-mcp` Service → Logs. Colour is on (`FORCE_COLOR=1`);
  remove it in `deploy.py` if the log viewer shows escape codes.
- **On your laptop (best for a live demo):** run the server locally and expose it with a tunnel,
  then point the `penn-fhir` MCP entry at the tunnel URL. The panels appear in your terminal.
  ```bash
  cd fhir-mcp && pip install -r requirements.txt
  export ENTRA_TENANT_ID=... MCP_API_APP_ID=... CLINICIAN_DEPARTMENTS='{"agastya.sen@truefoundry.com":"cardiology"}'
  python server.py                              # :8000
  cloudflared tunnel --url http://localhost:8000
  ```

## Demo script (one login)

1. **Show the source has many departments:** `python fhir-mcp/inventory.py` (all 16 patients by
   department), or `python fhir-mcp/inventory.py --except cardiology` (the 13 you must never see).
2. **Agent UI:** sign in, then ask *"Who am I and which department am I in?"* → `whoami` says Cardiology.
3. *"Show me my patients"* → 3 Cardiology patients. Terminal: token from you → mapping → CARDIOLOGY →
   3 rows returned, 13 withheld (Oncology 4, Neurology 3, …).
4. *"Get the records for MRN 20481733"* (an Oncology patient from the inventory) → the agent says
   you're not permitted. Terminal: red ACCESS DENIED panel, *belongs to ONCOLOGY; you are CARDIOLOGY*.
5. *"Get the records for MRN 10519022"* → full FHIR bundle for your own patient.
6. **TrueFoundry → MCP server traces:** every call shows the agent (`mock-agent`) and you.
7. Optional: change `CLINICIAN_DEPARTMENTS` to `oncology`, redeploy, ask again → now you see the
   Oncology patients and Cardiology is denied. Same login, same agent, only the mapping changed.

## Without the agent: prove it from the command line

```bash
cd fhir-mcp
TOKEN=$(az account get-access-token --scope "api://<mcp-api-app-id>/Tool.Write" --query accessToken -o tsv)
python prove_access.py https://penn-fhir-mcp.<base-domain>/mcp "$TOKEN"     # direct to the server
```

Prints who the server thinks you are, your patients, one allowed record read, and one rejected read
of an Oncology MRN. Through the gateway instead: use an `agent-service` token and the gateway URL
(see the script's docstring).

## Local test (no Entra needed)

```bash
cd fhir-mcp && pip install -r requirements.txt PyJWT cryptography
python test_local.py --show
```

Mints fake tokens and checks: no token / wrong scope → 401; your mapped department only; other
departments' MRNs rejected; unmapped user and app-only tokens rejected. `--show` prints the
server's terminal view.

## Files

```
fhir-mcp/
  server.py          Entra JWT auth (unchanged from mcp-obo-demo) + department rule + tools:
                     whoami, list_my_patients, get_patient(mrn), get_patient_records(mrn)
  data.py            16 synthetic FHIR R4 patients (Patient, Condition, Observation, Encounter)
  audit_view.py      the terminal panels
  inventory.py       data-owner view of the whole source, by department
  prove_access.py    command-line proof with a real token
  test_local.py      offline test with a fake JWKS
  deploy.py, Dockerfile, requirements.txt
agent/               copy of netp_agent/mock-agent; only AGENT_NAME, SYSTEM_PROMPT, UI hint, deploy defaults changed
```

Not included yet: the service-principal MRN lookup (Penn's scenario 1). The agent talks to a single
MCP server today, so that needs a small agent change and a second MCP entry with `client_credentials`.
