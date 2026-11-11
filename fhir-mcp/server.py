"""
Mock Epic FHIR MCP server, protected by Microsoft Entra ID JWTs.

Authentication is identical to mcp-obo-demo: every request must carry a bearer token that
  - is signed by Entra (verified against the tenant's JWKS)
  - has iss == https://login.microsoftonline.com/<TENANT_ID>/v2.0
  - has aud == the `mcp-api` app registration's Application (client) ID
  - carries the delegated scope `Tool.Write` in `scp`
In the demo that token is minted by the TrueFoundry gateway's on-behalf-of exchange, so it
still names the signed-in clinician (`preferred_username`, `oid`) and the acting app (`azp`).

What is new is authorization. The server reads the clinician from the token, looks up their
department in CLINICIAN_DEPARTMENTS, and only returns patients from that department. Every
decision is printed to the terminal (see audit_view.py).

    CLINICIAN_DEPARTMENTS='{"agastya.sen@truefoundry.com": "cardiology"}'

Keys are matched case-insensitively against `preferred_username` (an Entra object ID / `oid`
also works as a key). Values: cardiology, oncology, neurology, pediatrics, emergency.
Anyone not in the map gets nothing.
"""

import json
import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

import audit_view
import data

load_dotenv()

TENANT_ID = os.environ["ENTRA_TENANT_ID"]
AUDIENCE = os.environ["MCP_API_APP_ID"]  # bare GUID, not api://... (v2 tokens use the GUID in `aud`)
REQUIRED_SCOPE = os.environ.get("REQUIRED_SCOPE", "Tool.Write")

ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
# FASTMCP_TEST_JWKS is only for test_local.py; never set it in a real deployment.
JWKS_URI = os.environ.get("FASTMCP_TEST_JWKS") or f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"


def _load_mapping() -> dict[str, str]:
    raw = os.environ.get("CLINICIAN_DEPARTMENTS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"CLINICIAN_DEPARTMENTS is not valid JSON: {e}")
    mapping = {str(k).strip().lower(): str(v).strip().lower() for k, v in parsed.items()}
    unknown = sorted({d for d in mapping.values() if d not in data.DEPARTMENT_NAMES})
    if unknown:
        raise SystemExit(f"CLINICIAN_DEPARTMENTS has unknown department(s) {unknown}; use one of {sorted(data.DEPARTMENT_NAMES)}")
    return mapping


CLINICIAN_DEPARTMENTS = _load_mapping()


class EntraJWTVerifier(JWTVerifier):
    """
    JWTVerifier that compares scopes case-insensitively.

    Entra matches requested scopes case-insensitively but returns them in `scp` exactly as the
    scope was spelled when it was created on the app registration. Normalising both sides avoids
    a per-environment REQUIRED_SCOPE override.
    """

    def _extract_scopes(self, claims):  # type: ignore[override]
        return [s.lower() for s in super()._extract_scopes(claims)]


verifier = EntraJWTVerifier(
    jwks_uri=JWKS_URI,
    issuer=ISSUER,
    audience=AUDIENCE,
    required_scopes=[REQUIRED_SCOPE.lower()],  # fastmcp reads both `scope` and Entra's `scp`
)

mcp = FastMCP("penn-fhir-mock", auth=verifier)


# --- Well-known routes -------------------------------------------------------
@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
async def oauth_well_known(_: Request):
    return RedirectResponse(f"{ISSUER}/.well-known/openid-configuration", status_code=307)


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request):
    return JSONResponse({"status": "ok", "audience": AUDIENCE, "clinicians_mapped": len(CLINICIAN_DEPARTMENTS)})


# --- Identity + access rule --------------------------------------------------
def _caller() -> dict:
    """The clinician named by the verified token, plus their mapped department (or None)."""
    token = get_access_token()
    claims = token.claims if token else {}
    upn = claims.get("preferred_username") or claims.get("upn") or claims.get("email")
    oid = claims.get("oid")
    dept = None
    if upn and upn.lower() in CLINICIAN_DEPARTMENTS:
        dept = CLINICIAN_DEPARTMENTS[upn.lower()]
    elif oid and oid.lower() in CLINICIAN_DEPARTMENTS:
        dept = CLINICIAN_DEPARTMENTS[oid.lower()]
    return {
        "upn": upn, "name": claims.get("name"), "oid": oid, "azp": claims.get("azp"),
        "aud": claims.get("aud"), "scp": claims.get("scp"), "dept": dept,
        "delegated": bool(upn or oid), "claims": claims,
    }


def _require_department(tool: str, args: dict, c: dict) -> str:
    if not c["delegated"]:
        reason = "token carries no user (app-only token); patient data needs a clinician identity"
        audit_view.denied(tool, args, c, "caller must be a mapped clinician", reason)
        raise ToolError(f"Access denied: {reason}.")
    if not c["dept"]:
        reason = f"{c['upn'] or c['oid']} has no department mapping"
        audit_view.denied(tool, args, c, "caller must be a mapped clinician", reason)
        raise ToolError(f"Access denied: {reason}.")
    return c["dept"]


def _authorize_mrn(tool: str, mrn: str) -> tuple[dict, dict]:
    """Return (caller, patient) if the caller's department owns this MRN; otherwise log + raise."""
    mrn = mrn.strip()
    args = {"mrn": mrn}
    c = _caller()
    dept = _require_department(tool, args, c)
    rule = f"patient's department must equal caller's department ({data.DEPARTMENT_NAMES[dept]})"
    owner = data.department_of(mrn)
    if owner is None:
        audit_view.denied(tool, args, c, rule, f"MRN {mrn} does not exist in the data source", label="✖ NOT FOUND")
        raise ToolError(f"No patient found with MRN {mrn}.")
    if owner != dept:
        audit_view.denied(
            tool, args, c, rule,
            f"MRN {mrn} belongs to {data.DEPARTMENT_NAMES[owner].upper()}; "
            f"{c['upn']} is mapped to {data.DEPARTMENT_NAMES[dept].upper()}",
        )
        # The caller is told it's outside their department, not which department it is in.
        raise ToolError(
            f"Access denied: patient MRN {mrn} is not under your department "
            f"({data.DEPARTMENT_NAMES[dept]}). This attempt has been logged."
        )
    return c, data.PATIENTS[mrn]


# --- Tools -------------------------------------------------------------------
@mcp.tool
def list_my_patients() -> dict:
    """
    List the patients the signed-in clinician is allowed to see: everyone under the clinician's
    own department. Returns one summary row per patient (MRN, name, birth date, condition,
    latest lab, last encounter). Use get_patient_records for the full FHIR resources.
    """
    tool, c = "list_my_patients", _caller()
    dept = _require_department(tool, {}, c)
    rows = [data.summary_row(p) for p in data.patients_in(dept)]
    audit_view.allowed(tool, {}, c, f"Patient.managingOrganization == {data.DEPARTMENT_NAMES[dept]}", rows)
    return {"clinician": c["upn"], "department": data.DEPARTMENT_NAMES[dept], "count": len(rows), "patients": rows}


@mcp.tool
def get_patient(mrn: str) -> dict:
    """Fetch the FHIR Patient resource for a medical record number (MRN), if the clinician may see it."""
    c, p = _authorize_mrn("get_patient", mrn)
    audit_view.allowed("get_patient", {"mrn": mrn}, c,
                       f"patient's department must equal caller's department ({data.DEPARTMENT_NAMES[c['dept']]})",
                       [data.summary_row(p)])
    return p["patient"]


@mcp.tool
def get_patient_records(mrn: str) -> dict:
    """
    Fetch a FHIR Bundle for one patient by MRN: the Patient plus active Conditions, the latest lab
    Observation and the most recent Encounter. Only works for patients in the clinician's department.
    """
    c, p = _authorize_mrn("get_patient_records", mrn)
    audit_view.allowed("get_patient_records", {"mrn": mrn}, c,
                       f"patient's department must equal caller's department ({data.DEPARTMENT_NAMES[c['dept']]})",
                       [data.summary_row(p)], note=f"returned FHIR Bundle: Patient + {len(p['records'])} resources")
    return data.bundle([p["patient"], *p["records"]])


@mcp.tool
def whoami() -> dict:
    """
    Show who this call is running as, according to the verified Entra token, and which
    department that clinician is mapped to. `user` should be the signed-in person and
    `actor_app` the app the gateway exchanged as.
    """
    c = _caller()
    audit_view.info("whoami", c)
    claims = c["claims"]
    return {
        "mode": "delegated" if claims.get("oid") else "application",
        "user": c["upn"],
        "user_name": c["name"],
        "user_oid": c["oid"],  # stable per-user key; `sub` changes across exchanges
        "department": data.DEPARTMENT_NAMES.get(c["dept"]) if c["dept"] else None,
        "actor_app": claims.get("azp"),
        "aud": claims.get("aud"),
        "scp": claims.get("scp"),
        "iss": claims.get("iss"),
        "tenant": claims.get("tid"),
        "exp": claims.get("exp"),
    }


if __name__ == "__main__":
    audit_view.startup(AUDIENCE, CLINICIAN_DEPARTMENTS)
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        stateless_http=True,
    )
