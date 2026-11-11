"""
Data-owner view: print everything in the mock Epic data source, grouped by department.

This reads the dataset directly, with no token and no access rule, the way a DBA would look
at the table. Run it next to the demo to show that the source holds patients from many
departments, then show that the clinician only ever gets their own department's rows.

    python inventory.py                      # all departments
    python inventory.py --except cardiology  # only what a cardiology clinician must NOT see

Inside the deployed Service:  python inventory.py   (it is copied into the image)
"""

import argparse
import os

from rich import box
from rich.console import Console
from rich.table import Table

import data

console = Console(highlight=False, width=None if os.isatty(1) else 130)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--except", dest="exclude", choices=sorted(data.DEPARTMENT_NAMES), help="hide one department")
    args = ap.parse_args()

    counts = data.counts_by_department()
    shown = [d for d in data.DEPARTMENT_NAMES if d != args.exclude]
    console.print(f"[bold]Mock Epic data source[/] · {len(data.PATIENTS)} synthetic patients · {len(counts)} departments")
    if args.exclude:
        hidden = sum(counts[d] for d in shown)
        console.print(f"Showing the {hidden} patients outside {data.DEPARTMENT_NAMES[args.exclude]}: "
                      f"a {data.DEPARTMENT_NAMES[args.exclude]} clinician must never receive any of these rows.\n")

    for dept in shown:
        t = Table(title=f"{data.DEPARTMENT_NAMES[dept]} · {counts[dept]} patients", title_justify="left",
                  box=box.SIMPLE_HEAVY, header_style="bold", pad_edge=False)
        for col in ("mrn", "name", "birthDate", "condition", "latest_lab", "last_encounter"):
            t.add_column(col.replace("_", " "))
        for p in data.patients_in(dept):
            r = data.summary_row(p)
            t.add_row(r["mrn"], r["name"], r["birthDate"], r["condition"], r["latest_lab"], r["last_encounter"])
        console.print(t)


if __name__ == "__main__":
    main()
