"""
Terminal view of every access decision the server makes.

Each tool call prints a numbered panel: who the incoming token names, the department mapping
lookup, the rule applied, and the outcome. Allowed calls also print the rows returned; denied
calls print why. This goes to stdout, so it shows in the local terminal or in the TrueFoundry
Service logs.

Colour: on automatically in a real terminal. Set FORCE_COLOR=1 to keep colour in container logs,
or NO_COLOR=1 to turn it off. CONSOLE_WIDTH sets the width when stdout is not a terminal.
"""

from __future__ import annotations

import os
import time

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import data

console = Console(
    width=None if os.isatty(1) else int(os.environ.get("CONSOLE_WIDTH", "130")),
    force_terminal=True if os.environ.get("FORCE_COLOR") else None,
    highlight=False,
    soft_wrap=False,
)


def _short(v: str | None, n: int = 8) -> str:
    return "-" if not v else (v if len(v) <= n + 3 else f"{v[:n]}…")


def _caller_lines(c: dict) -> list[Text]:
    who = Text.assemble(("1  Incoming token from  ", "bold"), (c["name"] or "unknown", "bold cyan"),
                        "  <", (c["upn"] or "no preferred_username", "cyan"), ">")
    detail = Text(
        f"   oid {_short(c['oid'])} · acting app (azp) {_short(c['azp'])} · aud {_short(c['aud'])} ✓ · "
        f"scp {c['scp'] or '-'} · signature + issuer verified",
        style="dim",
    )
    lookup = Text.assemble(("2  Checking clinician → department mapping  ", "bold"),
                           (c["upn"] or "?", "cyan"), "  →  ",
                           (data.DEPARTMENT_NAMES.get(c["dept"], "NO MAPPING").upper() if c["dept"] else "NO MAPPING",
                            "bold green" if c["dept"] else "bold red"))
    return [who, detail, lookup]


def _rows_table(rows: list[dict], title: str) -> Table:
    t = Table(title=title, title_justify="left", box=box.SIMPLE_HEAVY, header_style="bold", pad_edge=False)
    for col in ("mrn", "name", "birthDate", "department", "condition", "latest_lab", "last_encounter"):
        t.add_column(col.replace("_", " "), overflow="fold")
    for r in rows:
        t.add_row(*(str(r[k]) for k in ("mrn", "name", "birthDate", "department", "condition", "latest_lab", "last_encounter")))
    return t


def _panel(tool: str, args: dict, body: list, ok: bool) -> Panel:
    arg_text = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
    return Panel(
        Group(*body),
        title=f"[bold]MCP tool call · {tool}({arg_text})[/]",
        subtitle=time.strftime("%H:%M:%S"),
        border_style="green" if ok else "red",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def allowed(tool: str, args: dict, caller: dict, rule: str, rows: list[dict], note: str | None = None) -> None:
    total = len(data.PATIENTS)
    others = {d: n for d, n in data.counts_by_department().items() if d != caller["dept"]}
    withheld = sum(others.values())
    body = _caller_lines(caller) + [
        Text.assemble(("3  Access rule  ", "bold"), rule),
        Text.assemble(("4  Result  ", "bold"), ("✔ ALLOWED", "bold green"),
                      f"  {len(rows)} row(s) returned"
                      + (f" · {withheld} of {total} patients in the source withheld ("
                         + ", ".join(f"{data.DEPARTMENT_NAMES[d]} {n}" for d, n in sorted(others.items())) + ")"
                         if tool == "list_my_patients" else "")),
    ]
    if note:
        body.append(Text(f"   {note}", style="dim"))
    console.print(_panel(tool, args, body, ok=True))
    if rows:
        console.print(_rows_table(rows, f"Rows returned to {caller['upn']}"))


def denied(tool: str, args: dict, caller: dict, rule: str, reason: str, label: str = "✖ ACCESS DENIED") -> None:
    body = _caller_lines(caller) + [
        Text.assemble(("3  Access rule  ", "bold"), rule),
        Text.assemble(("4  Result  ", "bold"), (label, "bold red"), f"  {reason}"),
        Text("   Nothing returned to the caller.", style="dim"),
    ]
    console.print(_panel(tool, args, body, ok=False))


def info(tool: str, caller: dict) -> None:
    body = _caller_lines(caller) + [Text.assemble(("3  Result  ", "bold"), "identity echoed back (no patient data)")]
    console.print(_panel(tool, {}, body, ok=True))


def startup(audience: str, mapping: dict[str, str]) -> None:
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold", pad_edge=False)
    t.add_column("clinician (preferred_username)")
    t.add_column("department")
    for upn, dept in mapping.items():
        t.add_row(upn, data.DEPARTMENT_NAMES.get(dept, f"{dept} (unknown!)"))
    counts = data.counts_by_department()
    src = ", ".join(f"{data.DEPARTMENT_NAMES[d]} {n}" for d, n in sorted(counts.items()))
    console.print(Panel(
        Group(Text(f"Mock Epic FHIR MCP server · accepts Entra tokens with aud {audience}"),
              Text(f"Data source: {len(data.PATIENTS)} synthetic patients across {len(counts)} departments ({src})"),
              t if mapping else Text("No clinicians mapped (CLINICIAN_DEPARTMENTS is empty): every data call will be denied.", style="bold red")),
        title="[bold]penn-fhir-mcp starting[/]", box=box.ROUNDED, border_style="blue",
    ))
