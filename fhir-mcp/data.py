"""
Synthetic, FHIR R4-shaped patient data standing in for Epic.

Every name, MRN, date and value here is made up. Each patient belongs to one department
(Patient.managingOrganization), and that department is what the access rule in server.py
checks against the signed-in clinician's department.
"""

from __future__ import annotations

from collections import Counter

MRN_SYSTEM = "urn:oid:1.2.840.114350.mock.penn.mrn"  # mock identifier system, not a real Epic OID

# (mrn, given, family, gender, birthDate, department, condition, (lab, value, unit), encounter reason, encounter date)
_ROWS = [
    # Cardiology
    ("10234871", "Marla", "Quintero", "female", "1958-03-14", "cardiology", "Atrial fibrillation (I48.91)", ("Troponin I", 0.02, "ng/mL"), "Palpitations follow-up", "2026-10-28"),
    ("10519022", "Desmond", "Achterberg", "male", "1949-11-02", "cardiology", "Heart failure, reduced EF (I50.2)", ("NT-proBNP", 1840, "pg/mL"), "Dyspnea on exertion", "2026-11-03"),
    ("10877345", "Ileana", "Brookfield", "female", "1971-06-21", "cardiology", "Essential hypertension (I10)", ("LDL cholesterol", 162, "mg/dL"), "Blood pressure review", "2026-11-06"),
    # Oncology
    ("20481733", "Theo", "Marchetti", "male", "1963-01-30", "oncology", "Non-small cell lung cancer (C34.90)", ("CEA", 7.9, "ng/mL"), "Chemotherapy cycle 3", "2026-11-04"),
    ("20619980", "Priyanka", "Oduya", "female", "1980-09-12", "oncology", "Breast cancer, ER+ (C50.919)", ("CA 15-3", 41, "U/mL"), "Post-surgical review", "2026-10-30"),
    ("20733514", "Gunnar", "Hollis", "male", "1955-04-08", "oncology", "Chronic lymphocytic leukemia (C91.10)", ("WBC", 38.2, "10*3/uL"), "Hematology consult", "2026-11-07"),
    ("20910267", "Rosalind", "Ferraro", "female", "1968-12-01", "oncology", "Colon cancer, stage II (C18.9)", ("Hemoglobin", 10.4, "g/dL"), "Surveillance CT review", "2026-11-02"),
    # Neurology
    ("30144829", "Kwame", "Lindqvist", "male", "1975-07-19", "neurology", "Epilepsy, focal (G40.109)", ("Levetiracetam level", 18, "ug/mL"), "Seizure medication review", "2026-11-05"),
    ("30388106", "Beatriz", "Okonkwo", "female", "1987-02-25", "neurology", "Multiple sclerosis (G35)", ("Vitamin D, 25-OH", 22, "ng/mL"), "MRI results discussion", "2026-10-29"),
    ("30561447", "Harold", "Vasquez-Tran", "male", "1944-10-10", "neurology", "Parkinson's disease (G20)", ("Creatinine", 1.1, "mg/dL"), "Gait assessment", "2026-11-01"),
    # Pediatrics
    ("40227390", "Lily", "Nakamura-Shaw", "female", "2016-05-03", "pediatrics", "Asthma, mild persistent (J45.30)", ("Eosinophils", 0.6, "10*3/uL"), "Asthma action plan", "2026-11-06"),
    ("40458812", "Mateo", "Delacroix", "male", "2019-08-17", "pediatrics", "Type 1 diabetes (E10.9)", ("Hemoglobin A1c", 8.1, "%"), "Insulin titration", "2026-11-04"),
    ("40690135", "Ada", "Kowalczyk", "female", "2012-01-28", "pediatrics", "ADHD, combined type (F90.2)", ("Ferritin", 31, "ng/mL"), "School performance check-in", "2026-10-31"),
    # Emergency medicine
    ("50173264", "Victor", "Esposito", "male", "1990-03-09", "emergency", "Closed fracture, distal radius (S52.501A)", ("Lactate", 1.4, "mmol/L"), "Fall from bicycle", "2026-11-08"),
    ("50392718", "Naomi", "Fairweather", "female", "1983-12-22", "emergency", "Acute appendicitis (K35.80)", ("WBC", 15.6, "10*3/uL"), "Right lower quadrant pain", "2026-11-09"),
    ("50624903", "Samir", "Haddad", "male", "1972-07-04", "emergency", "Chest pain, unspecified (R07.9)", ("Troponin I", 0.01, "ng/mL"), "Chest pain rule-out", "2026-11-09"),
]

DEPARTMENT_NAMES = {
    "cardiology": "Cardiology",
    "oncology": "Oncology",
    "neurology": "Neurology",
    "pediatrics": "Pediatrics",
    "emergency": "Emergency Medicine",
}


def _patient(r) -> dict:
    mrn, given, family, gender, dob, dept = r[:6]
    return {
        "resourceType": "Patient",
        "id": f"pat-{mrn}",
        "identifier": [{"system": MRN_SYSTEM, "type": {"text": "MRN"}, "value": mrn}],
        "name": [{"use": "official", "family": family, "given": [given]}],
        "gender": gender,
        "birthDate": dob,
        "managingOrganization": {"reference": f"Organization/{dept}", "display": DEPARTMENT_NAMES[dept]},
        "meta": {"tag": [{"code": "SYNTHETIC", "display": "Synthetic demo data"}]},
    }


def _records(r) -> list[dict]:
    mrn, _, _, _, _, dept, condition, (lab, value, unit), reason, when = r
    subject = {"reference": f"Patient/pat-{mrn}"}
    return [
        {"resourceType": "Condition", "id": f"cond-{mrn}", "subject": subject,
         "clinicalStatus": {"text": "active"}, "code": {"text": condition}},
        {"resourceType": "Observation", "id": f"obs-{mrn}", "subject": subject, "status": "final",
         "category": [{"text": "laboratory"}], "code": {"text": lab},
         "valueQuantity": {"value": value, "unit": unit}, "effectiveDateTime": f"{when}T09:30:00Z"},
        {"resourceType": "Encounter", "id": f"enc-{mrn}", "subject": subject, "status": "finished",
         "class": {"code": "AMB" if dept != "emergency" else "EMER"},
         "reasonCode": [{"text": reason}], "period": {"start": f"{when}T09:00:00Z"},
         "serviceProvider": {"display": DEPARTMENT_NAMES[dept]}},
    ]


PATIENTS: dict[str, dict] = {r[0]: {"dept": r[5], "row": r, "patient": _patient(r), "records": _records(r)} for r in _ROWS}


def department_of(mrn: str) -> str | None:
    p = PATIENTS.get(mrn)
    return p["dept"] if p else None


def patients_in(dept: str) -> list[dict]:
    return [p for p in PATIENTS.values() if p["dept"] == dept]


def counts_by_department() -> Counter:
    return Counter(p["dept"] for p in PATIENTS.values())


def summary_row(p: dict) -> dict:
    """One flat row per patient, for terminal tables and list results."""
    mrn, given, family, gender, dob, dept, condition, (lab, value, unit), reason, when = p["row"]
    return {
        "mrn": mrn, "name": f"{given} {family}", "gender": gender, "birthDate": dob,
        "department": DEPARTMENT_NAMES[dept], "condition": condition,
        "latest_lab": f"{lab} {value} {unit}", "last_encounter": f"{when} · {reason}",
    }


def bundle(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "searchset", "total": len(entries),
            "entry": [{"resource": e} for e in entries]}
