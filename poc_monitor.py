#!/usr/bin/env python3
"""
DPR Workflow Monitor
====================

Scans preservation JOB_ID folders and regenerates an operator-facing Excel
workbook (DPR_Master.xlsx). The workbook is a read model/dashboard only; the
JSON records and preservation evidence remain authoritative.

Logical workflow order:
    Preparation -> DROID -> Conversion -> Validation -> QA -> Metadata
    -> Packaging -> Ingest -> OCFL

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import os
import time
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

JOB_RE = re.compile(r"^\d{4}_\d{6}$")

STAGES = [
    "Preparation", "DROID", "Conversion", "Validation", "QA",
    "Metadata", "Packaging", "Ingest", "OCFL",
]

# Historical PoC numbering is deliberately preserved for compatibility.
FILE_PATTERNS = {
    "Preparation": ["*_poc1_processing_record.json"],
    "DROID": ["*_poc5_characterization_record.json"],
    "Conversion": ["*_poc2_conversion_record.json"],
    "Validation": ["*_poc3_validation_record.json"],
    "QA": ["*_poc4_qa_record.json"],
    "Metadata": ["*_poc6_metadata_record.json", "*_poc6_dome_record.json"],
    "Packaging": ["*_poc7*.json"],
    "Ingest": ["*_poc8*.json"],
    "OCFL": ["*_poc9*.json"],
}

PASS_TOKENS = {
    "Preparation": ["STEP_1_PREPARED_FIXITY_VERIFIED"],
    "DROID": ["STEP_5_DROID_CHARACTERIZED", "STEP_SOURCE_FORMATS_CHARACTERIZED"],
    "Conversion": ["STEP_2_MASTERS_CREATED_UNVALIDATED"],
    "Validation": ["STEP_3_VERAPDF_PASSED"],
    "QA": ["STEP_4_AUTO_QA_CLEAR"],
    "Metadata": ["STEP_6_METADATA_PROJECTED", "STEP_6_DOME_SCHEMA_VALID"],
    "Packaging": ["STEP_7_BAGIT_SIP_STAGED_VALID"],
    "Ingest": ["READY_FOR_OCFL_INGEST", "STEP_8_PACKAGE_FIXITY_VERIFIED"],
    "OCFL": ["STEP_9_OCFL_AIP_COMMITTED"],
}

FAIL_TOKENS = {
    "Preparation": ["STEP_1_PREPARATION_FAILED", "FAILED", "FAIL"],
    "Validation": ["STEP_3_VERAPDF_FAILED_HOLD", "FAILED", "FAIL"],
    "QA": ["STEP_4_QA_FLAGGED_HOLD", "REVIEW", "FLAGGED"],
    "Packaging": ["FAILED", "FAIL", "INVALID", "HOLD"],
    "Ingest": ["FAILED", "FAIL", "HOLD", "REJECT"],
    "OCFL": ["FAILED", "FAIL", "HOLD"],
}


@dataclass
class StageResult:
    display: str
    raw_status: str = ""
    record_path: str = ""
    updated: str = ""
    error: str = ""
    error_type: str = ""
    error_folder: str = ""
    error_file: str = ""
    error_component: str = ""
    error_location: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def find_record(docs: Path, patterns: list[str]) -> Path | None:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(docs.glob(pattern))
    matches = sorted(set(matches), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return matches[0] if matches else None


def find_record_recursive(root: Path | None, job_id: str, patterns: list[str]) -> Path | None:
    """Find the newest matching record for one JOB_ID anywhere below root."""
    if root is None or not root.exists():
        return None

    matches: list[Path] = []
    for pattern in patterns:
        # Prefer JOB_ID-prefixed records but retain compatibility with generic patterns.
        prefixed = pattern if pattern.startswith(job_id) else f"{job_id}{pattern[1:]}" if pattern.startswith("*") else pattern
        matches.extend(root.rglob(prefixed))
        if prefixed != pattern:
            matches.extend(root.rglob(pattern))

    # Only accept files that clearly belong to this JOB_ID.
    filtered = []
    for p in set(matches):
        if not p.is_file():
            continue
        if job_id in p.name or job_id in p.parts:
            filtered.append(p)

    filtered.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return filtered[0] if filtered else None


def expected_aip_path(aip_root: Path | None, job_id: str) -> Path | None:
    """Return the expected OCFL object path using the PoC bucket structure."""
    if aip_root is None:
        return None
    try:
        year, number = job_id.split("_", 1)
        bucket = f"{(int(number) // 1000) * 1000:06d}"
        return aip_root / year / bucket / job_id
    except Exception:
        return None


def find_aip_object(aip_root: Path | None, job_id: str) -> Path | None:
    """Locate a committed AIP object, preferring the expected bucket path."""
    if aip_root is None or not aip_root.exists():
        return None

    expected = expected_aip_path(aip_root, job_id)
    if expected and expected.is_dir():
        return expected

    # Fallback for future hierarchy changes.
    try:
        for p in aip_root.rglob(job_id):
            if p.is_dir() and p.name == job_id:
                return p
    except Exception:
        pass
    return None



def _first_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("message", "error", "detail", "reason", "path", "file", "report"):
            text = _first_text(value.get(key))
            if text:
                return text
    return ""


def _find_nested(data: Any, keys: tuple[str, ...]) -> str:
    """Find the first useful value for one of keys anywhere in a JSON record."""
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                text = _first_text(data[key])
                if text:
                    return text
        for value in data.values():
            text = _find_nested(value, keys)
            if text:
                return text
    elif isinstance(data, list):
        for value in data:
            text = _find_nested(value, keys)
            if text:
                return text
    return ""


def extract_error_context(stage: str, data: dict[str, Any], record_path: Path) -> dict[str, str]:
    """Normalize heterogeneous PoC error evidence into operator-facing fields."""
    error = _first_text(data.get("error")) or _first_text(data.get("errors")) or _first_text(data.get("message"))
    error_type = _first_text(data.get("error_type")) or _first_text(data.get("failure_type"))

    # Stage-specific evidence often carries a more useful affected file/report.
    file_value = _find_nested(
        data,
        ("error_file", "failed_file", "master_path", "master", "source_path",
         "source", "file_path", "path", "report_path", "report"),
    )
    component = _find_nested(
        data,
        ("error_component", "component", "element", "field", "xpath",
         "check", "rule", "test", "flavour"),
    )
    explicit_location = _find_nested(
        data,
        ("error_location", "location", "line", "xpath", "context"),
    )

    # If a path-like value is available, split it for easy filtering in Excel.
    folder = ""
    filename = ""
    if file_value:
        try:
            p = Path(file_value)
            filename = p.name
            folder = str(p.parent) if str(p.parent) != "." else ""
        except Exception:
            filename = file_value

    if not error_type:
        if stage == "Validation":
            error_type = "PDF_A_VALIDATION"
        elif stage == "QA":
            error_type = "QA_REVIEW"
        elif stage == "Metadata":
            error_type = "METADATA_OR_XSD"
        elif stage == "Packaging":
            error_type = "BAGIT_PACKAGING"
        elif stage == "Ingest":
            error_type = "INGEST_VERIFICATION"
        elif stage == "OCFL":
            error_type = "OCFL_INGEST"
        elif error:
            error_type = f"{stage.upper()}_ERROR"

    location_parts = [x for x in (folder, filename, component, explicit_location) if x]
    return {
        "error": error,
        "error_type": error_type,
        "error_folder": folder,
        "error_file": filename,
        "error_component": component,
        "error_location": " | ".join(dict.fromkeys(location_parts)),
    }


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write live state atomically so readers never see a half-written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def write_live_state(path: Path, rows: list[dict[str, Any]]) -> None:
    """Machine-readable authoritative monitor snapshot, safe to read while Excel is open."""
    atomic_write_json(path, {
        "schema": "DPR_WORKFLOW_STATE_V1",
        "generated_utc": utc_now(),
        "job_count": len(rows),
        "jobs": rows,
    })

def status_from_record(stage: str, path: Path | None) -> StageResult:
    if path is None:
        return StageResult("—")

    data = read_json(path)
    raw = str(data.get("status") or data.get("disposition") or "").strip()
    upper = raw.upper()

    # Successful stage markers first.
    if any(token in upper for token in PASS_TOKENS.get(stage, [])):
        display = "PASS" if stage != "QA" else "CLEAR"
    # Human review is a special state, not a generic failure.
    elif stage == "QA" and any(token in upper for token in FAIL_TOKENS.get("QA", [])):
        display = "REVIEW"
    elif any(token in upper for token in FAIL_TOKENS.get(stage, [])):
        display = "FAILED"
    elif raw:
        display = raw
    else:
        # Record exists but has no recognized status. Surface that fact instead of guessing.
        display = "RECORDED"

    updated = ""
    for key in ("created_utc", "created_at", "timestamp", "completed_utc"):
        if data.get(key):
            updated = str(data[key])
            break
    if not updated:
        updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")

    ctx = extract_error_context(stage, data, path)
    return StageResult(
        display, raw, str(path), updated,
        ctx["error"], ctx["error_type"], ctx["error_folder"],
        ctx["error_file"], ctx["error_component"], ctx["error_location"],
    )


def apply_blocked_states(stages: dict[str, StageResult]) -> None:
    """
    Apply operator-facing BLOCKED states after a hard gate fails/holds.

    Existing successful/failed evidence is never overwritten. BLOCKED is only
    applied to stages that have no record yet, making the workflow intent clear:
    a failed JOB_ID must not continue while other JOB_IDs may proceed.
    """
    def block_from(stage_names: list[str]) -> None:
        for name in stage_names:
            if stages[name].display in ("—", "RECORDED"):
                stages[name].display = "BLOCKED"

    if stages["Preparation"].display == "FAILED":
        block_from(["DROID", "Conversion", "Validation", "QA", "Metadata", "Packaging", "Ingest", "OCFL"])
        return

    if stages["Validation"].display == "FAILED":
        block_from(["QA", "Metadata", "Packaging", "Ingest", "OCFL"])
        return

    if stages["QA"].display == "REVIEW":
        block_from(["Metadata", "Packaging", "Ingest", "OCFL"])
        return

    if stages["Packaging"].display == "FAILED":
        block_from(["Ingest", "OCFL"])
        return

    if stages["Ingest"].display == "FAILED":
        block_from(["OCFL"])




def load_standard_error(workflow_root: Path, job_id: str) -> dict[str, Any] | None:
    """Read the newest standardized DPR_ERROR_V1 record for this JOB_ID."""
    d = workflow_root / "error-records" / job_id
    if not d.is_dir():
        return None
    records: list[tuple[float, dict[str, Any], Path]] = []
    for p in d.glob("*_error.json"):
        data = read_json(p)
        if isinstance(data, dict) and data.get("schema") == "DPR_ERROR_V1":
            try:
                mt = p.stat().st_mtime
            except OSError:
                mt = 0.0
            records.append((mt, data, p))
    if not records:
        return None
    _, data, path = max(records, key=lambda x: x[0])
    data = dict(data)
    data["_record_path"] = str(path)
    return data


def standardized_error_summary(data: dict[str, Any] | None) -> dict[str, str]:
    if not data:
        return {}
    folder = str(data.get("error_folder") or "")
    filename = str(data.get("error_file") or "")
    component = str(data.get("error_component") or "")
    location = str(data.get("error_location") or "")
    if not location:
        location = " | ".join(x for x in (folder, filename, component) if x)
    return {
        "Failed Step": str(data.get("stage") or ""),
        "Error Type": str(data.get("error_type") or ""),
        "Error Folder": folder,
        "Error File": filename,
        "Error Component": component,
        "Error Location": location,
        "Error Detail": str(data.get("error_message") or ""),
        "Error Record": str(data.get("_record_path") or ""),
    }

def derive_error_summary(stages: dict[str, StageResult]) -> dict[str, str]:
    """Return the most actionable failing/held stage and its normalized location."""
    priority = ("Preparation", "DROID", "Conversion", "Validation", "QA",
                "Metadata", "Packaging", "Ingest", "OCFL")
    for name in priority:
        s = stages[name]
        if s.display in ("FAILED", "REVIEW") or "FAIL" in s.display.upper() or "HOLD" in s.display.upper():
            return {
                "Failed Step": name,
                "Error Type": s.error_type,
                "Error Folder": s.error_folder,
                "Error File": s.error_file,
                "Error Component": s.error_component,
                "Error Location": s.error_location or s.record_path,
                "Error Detail": s.error,
                "Error Record": s.record_path,
            }
    return {
        "Failed Step": "", "Error Type": "", "Error Folder": "", "Error File": "",
        "Error Component": "", "Error Location": "", "Error Detail": "", "Error Record": "",
    }

def derive_overall(stages: dict[str, StageResult]) -> tuple[str, str, str]:
    """Return (overall_status, current_stage, action_required)."""
    # Failure / hold states have priority.
    if stages["Preparation"].display == "FAILED":
        detail = stages["Preparation"].error
        action = "Investigate PoC 1 preparation failure; this JOB_ID is blocked from downstream processing"
        if detail:
            action += f": {detail}"
        return "Preparation Failed", "Preparation", action
    if stages["Validation"].display == "FAILED":
        return "Validation Failed", "Validation", "Investigate veraPDF failure; this JOB_ID is blocked from downstream processing"
    if stages["QA"].display == "REVIEW":
        return "QA Review Required", "QA", "Human review required; this JOB_ID is blocked until review is resolved"
    if stages["Packaging"].display == "FAILED":
        return "Packaging Failed", "Packaging", "Investigate BagIt/package failure; ingest is blocked"
    if stages["Ingest"].display == "FAILED":
        return "Ingest Failed / On Hold", "Ingest", "Investigate ingest verification failure; OCFL commit is blocked"
    if stages["OCFL"].display == "FAILED":
        return "OCFL Commit Failed", "OCFL", "Investigate OCFL commit failure"

    if stages["OCFL"].display == "PASS":
        return "Complete", "Complete", "None"

    # Next expected logical stage.
    sequence = [
        ("Preparation", "Ready for Preparation", "Run PoC 1"),
        ("DROID", "Ready for DROID", "Run source characterization before conversion"),
        ("Conversion", "Ready for Conversion", "Run PDF/A conversion"),
        ("Validation", "Ready for Validation", "Run veraPDF validation"),
        ("QA", "Ready for QA", "Run automated QA / review"),
        ("Metadata", "Ready for Metadata", "Generate DOME / PREMIS / METS"),
        ("Packaging", "Ready for Packaging", "Build and validate BagIt SIP"),
        ("Ingest", "Ready for Ingest", "Run independent ingest verification"),
        ("OCFL", "Ready for OCFL", "Commit AIP to OCFL storage"),
    ]

    for stage, overall, action in sequence:
        if stages[stage].display in ("—", "RECORDED"):
            return overall, stage, action

    return "In Progress", "Unknown", "Review technical records"


def latest_update(stages: dict[str, StageResult], object_dir: Path) -> str:
    vals = [s.updated for s in stages.values() if s.updated]
    if vals:
        return max(vals)
    if object_dir.exists():
        return datetime.fromtimestamp(object_dir.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    return ""


def scan_job(
    object_dir: Path,
    workflow_root: Path,
    staging_dir: Path | None = None,
    ingest_records_dir: Path | None = None,
    aip_root: Path | None = None,
    poc1_failure_record: Path | None = None,
) -> dict[str, Any]:
    job_id = object_dir.name
    docs = object_dir / "documentation"

    stages: dict[str, StageResult] = {}

    # PoC 1, DROID, PoC 2, PoC 3, PoC 4 and PoC 6 evidence lives with the
    # preparation object.
    for stage in ("Preparation", "DROID", "Conversion", "Validation", "QA", "Metadata"):
        rec = find_record(docs, FILE_PATTERNS[stage]) if docs.exists() else None
        stages[stage] = status_from_record(stage, rec)

    # PoC 7 packaging evidence lives in/under staging.
    packaging_rec = find_record_recursive(staging_dir, job_id, FILE_PATTERNS["Packaging"])
    stages["Packaging"] = status_from_record("Packaging", packaging_rec)

    # PoC 8 ingest verification evidence is written to ingest-records.
    ingest_rec = find_record_recursive(ingest_records_dir, job_id, FILE_PATTERNS["Ingest"])
    stages["Ingest"] = status_from_record("Ingest", ingest_rec)

    # PoC 9 OCFL ingest evidence is also written to ingest-records.
    ocfl_rec = find_record_recursive(ingest_records_dir, job_id, FILE_PATTERNS["OCFL"])
    stages["OCFL"] = status_from_record("OCFL", ocfl_rec)

    # Strengthen OCFL monitoring: a PASS record should correspond to a materialized AIP.
    aip_object = find_aip_object(aip_root, job_id)
    if stages["OCFL"].display == "PASS" and aip_root is not None and aip_root.exists():
        if aip_object is None:
            stages["OCFL"] = StageResult(
                "FAILED",
                raw_status="OCFL_RECORD_PASS_BUT_AIP_NOT_FOUND",
                record_path=stages["OCFL"].record_path,
                updated=stages["OCFL"].updated,
                error="PoC 9 record indicates commit, but final AIP object folder was not found",
            )

    # A PoC 1 failure can happen before the object documentation folder exists.
    # In that case use the external failure record written under _poc1_failures.
    if stages["Preparation"].display in ("—", "RECORDED") and poc1_failure_record:
        stages["Preparation"] = status_from_record("Preparation", poc1_failure_record)

    # UUID from PoC 1 is the canonical object UUID in this PoC tunnel.
    uuid_value = ""
    p1 = find_record(docs, FILE_PATTERNS["Preparation"]) if docs.exists() else None
    if p1:
        p1data = read_json(p1)
        uuid_value = str(p1data.get("folder_uuid") or p1data.get("uuid") or "")

    # Convert downstream empty stages into explicit BLOCKED states after
    # validation/QA/package/ingest failures or holds.
    apply_blocked_states(stages)

    overall, current_stage, action = derive_overall(stages)
    error_summary = derive_error_summary(stages)
    standard_error = standardized_error_summary(load_standard_error(workflow_root, job_id))
    if standard_error:
        error_summary = standard_error
        overall = "FAILED"
        current_stage = standard_error["Failed Step"] or current_stage
        action = "Review standardized error record and correct the reported file/component."

    return {
        "JOB_ID": job_id,
        "UUID": uuid_value,
        **{stage: stages[stage].display for stage in STAGES},
        "Overall Status": overall,
        "Current Stage": current_stage,
        "Action Required": action,
        **error_summary,
        "Last Updated": latest_update(stages, object_dir),
        "Object Folder": str(object_dir.resolve()),
        "Final AIP Location": str(aip_object.resolve()) if aip_object else "",
    }


def scan_failure_attempt(
    preparation_dir: Path,
    workflow_root: Path,
    record_path: Path,
) -> dict[str, Any] | None:
    """Create a dashboard row for one failed PoC 1 CSV attempt.

    Failure attempts are rows in their own right, even when the same JOB_ID also
    has a successful preparation object.  This lets DPR_Master reflect the input
    row count instead of collapsing everything to unique JOB_ID folders.
    """
    data = read_json(record_path)
    job_id = str(data.get("job_id") or "").strip()
    if not JOB_RE.match(job_id):
        return None

    stages = {name: StageResult("—") for name in STAGES}
    stages["Preparation"] = status_from_record("Preparation", record_path)
    apply_blocked_states(stages)
    overall, current_stage, action = derive_overall(stages)
    error_summary = derive_error_summary(stages)
    standard_error = standardized_error_summary(load_standard_error(workflow_root, job_id))
    if standard_error:
        error_summary = standard_error
        overall = "FAILED"
        current_stage = standard_error["Failed Step"] or current_stage
        action = "Review standardized error record and correct the reported file/component."


    csv_line = data.get("csv_line")
    if csv_line:
        action = f"CSV line {csv_line}: {action}"

    return {
        "JOB_ID": job_id,
        "UUID": "",
        **{stage: stages[stage].display for stage in STAGES},
        "Overall Status": overall,
        "Current Stage": current_stage,
        "Action Required": action,
        **error_summary,
        "Last Updated": stages["Preparation"].updated,
        "Object Folder": str((preparation_dir / job_id).resolve()),
        "Final AIP Location": "",
        "_failure_csv_line": int(csv_line) if str(csv_line or "").isdigit() else 10**9,
    }


def scan(
    preparation_dir: Path,
    workflow_root: Path,
    staging_dir: Path | None = None,
    ingest_records_dir: Path | None = None,
    aip_root: Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not preparation_dir.is_dir():
        raise FileNotFoundError(f"Preparation directory does not exist: {preparation_dir}")

    # First, scan every materialized JOB_ID object normally.
    for p in sorted(preparation_dir.iterdir()):
        if p.is_dir() and JOB_RE.match(p.name):
            row = scan_job(
                p,
                workflow_root=workflow_root,
                staging_dir=staging_dir,
                ingest_records_dir=ingest_records_dir,
                aip_root=aip_root,
            )
            row["_failure_csv_line"] = -1
            rows.append(row)

    # Then append EVERY PoC 1 failed CSV attempt.  Do not deduplicate by JOB_ID:
    # one JOB_ID can have a successful object plus one or more failed CSV rows.
    failure_dir = preparation_dir / "_poc1_failures"
    if failure_dir.is_dir():
        patterns = [
            "*_line_*_poc1_failure_record.json",       # current row-level format
            "*_poc1_processing_record.json",           # backward-compatible format
        ]
        seen_paths: set[Path] = set()
        for pattern in patterns:
            for rec in sorted(failure_dir.glob(pattern)):
                if rec in seen_paths:
                    continue
                seen_paths.add(rec)
                row = scan_failure_attempt(preparation_dir, workflow_root, rec)
                if row is not None:
                    rows.append(row)

    # Keep duplicate JOB_IDs adjacent; successful object first, then failed CSV
    # attempts in CSV-line order.
    rows.sort(key=lambda r: (r["JOB_ID"], r.get("_failure_csv_line", -1)))
    for row in rows:
        row.pop("_failure_csv_line", None)
    return rows


# ---------------- Minimal XLSX writer (standard library only) ----------------
# The monitor regenerates the workbook on each run, so the spreadsheet remains
# a dashboard/read model rather than an authoritative manually edited record.

HEADERS = [
    "JOB_ID", "UUID", "Preparation", "DROID", "Conversion", "Validation",
    "QA", "Metadata", "Packaging", "Ingest", "OCFL", "Overall Status",
    "Current Stage", "Action Required",
    "Failed Step", "Error Type", "Error Folder", "Error File",
    "Error Component", "Error Location", "Error Detail", "Error Record",
    "Last Updated", "Object Folder", "Final AIP Location",
]


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def xml_text(v: Any) -> str:
    return escape("" if v is None else str(v))


def style_for(value: str, col_name: str) -> int:
    v = str(value).upper()
    if col_name in STAGES or col_name == "Overall Status":
        if v in ("PASS", "CLEAR", "COMPLETE"):
            return 2  # green
        if "FAIL" in v:
            return 3  # red
        if "REVIEW" in v or "HOLD" in v or v == "BLOCKED":
            return 4  # orange
        if v in ("—", ""):
            return 5  # grey
        return 6      # blue/in progress
    return 1


def sheet_xml(rows: list[dict[str, Any]]) -> str:
    data_rows = [HEADERS] + [[r.get(h, "") for h in HEADERS] for r in rows]
    xml_rows = []
    for r_idx, row in enumerate(data_rows, 1):
        cells = []
        for c_idx, value in enumerate(row, 1):
            ref = f"{col_letter(c_idx)}{r_idx}"
            if r_idx == 1:
                style = 7
            else:
                style = style_for(value, HEADERS[c_idx - 1])
            cells.append(f'<c r="{ref}" t="inlineStr" s="{style}"><is><t>{xml_text(value)}</t></is></c>')
        xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    last = max(2, len(data_rows))
    width_map = {
        "JOB_ID": 16, "UUID": 38, "Overall Status": 24, "Current Stage": 18,
        "Action Required": 42, "Failed Step": 16, "Error Type": 24,
        "Error Folder": 48, "Error File": 38, "Error Component": 28,
        "Error Location": 58, "Error Detail": 70, "Error Record": 58,
        "Last Updated": 23, "Object Folder": 55, "Final AIP Location": 55,
    }
    widths = [width_map.get(h, 14) for h in HEADERS]
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths, 1)
    )
    last_col = col_letter(len(HEADERS))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>{cols}</cols>
  <sheetData>{''.join(xml_rows)}</sheetData>
  <autoFilter ref="A1:{last_col}{last}"/>
</worksheet>'''


def legend_xml() -> str:
    rows = [
        ["DPR Master — Status Legend", "Meaning"],
        ["PASS / CLEAR / Complete", "Stage completed successfully"],
        ["FAILED", "Stage failed; workflow should not continue"],
        ["REVIEW", "Human QA review required"],
        ["BLOCKED", "Stage must not run because an earlier gate failed or requires review"],
        ["Ready for …", "Previous stages complete; this is the next expected stage"],
        ["—", "No stage record found"],
        ["Important", "This workbook is an operator dashboard. JSON/evidence records remain authoritative."],
        ["Logical order", "Preparation → DROID → Conversion → Validation → QA → Metadata → Packaging → Ingest → OCFL"],
    ]
    xr=[]
    for i,row in enumerate(rows,1):
        cs=[]
        for j,v in enumerate(row,1):
            style=7 if i==1 else (2 if i==2 else 3 if i==3 else 4 if i==4 else 1)
            ref=f"{col_letter(j)}{i}"
            cs.append(f'<c r="{ref}" t="inlineStr" s="{style}"><is><t>{xml_text(v)}</t></is></c>')
        xr.append(f'<row r="{i}">{"".join(cs)}</row>')
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<cols><col min="1" max="1" width="28" customWidth="1"/><col min="2" max="2" width="90" customWidth="1"/></cols>
<sheetData>{''.join(xr)}</sheetData></worksheet>'''


def write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3">
 <font><sz val="10"/><name val="Aptos"/></font>
 <font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Aptos"/></font>
 <font><b/><sz val="10"/><name val="Aptos"/></font>
</fonts>
<fills count="7">
 <fill><patternFill patternType="none"/></fill>
 <fill><patternFill patternType="gray125"/></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFD9EAD3"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFF4CCCC"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFFCE5CD"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2"><border/><border><left style="thin"><color rgb="FFD9D9D9"/></left><right style="thin"><color rgb="FFD9D9D9"/></right><top style="thin"><color rgb="FFD9D9D9"/></top><bottom style="thin"><color rgb="FFD9D9D9"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="8">
 <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
 <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
 <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
 <xf numFmtId="0" fontId="2" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
 <xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
 <xf numFmtId="0" fontId="2" fillId="6" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
 <xf numFmtId="0" fontId="1" fillId="6" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="DPR Master" sheetId="1" r:id="rId1"/><sheet name="Status Legend" sheetId="2" r:id="rId2"/></sheets>
</workbook>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''

    wb_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml(rows))
        z.writestr("xl/worksheets/sheet2.xml", legend_xml())


def refresh_once(args) -> tuple[list[dict[str, Any]], Path]:
    preparation_dir = args.preparation_dir.resolve()
    workflow_root = preparation_dir.parent
    staging_dir = (args.staging_dir or (workflow_root / "staging")).resolve()
    ingest_records_dir = (args.ingest_records_dir or (workflow_root / "ingest-records")).resolve()
    aip_root = (args.aip_root or (workflow_root / "AIP")).resolve()
    state_json = (args.state_json or (workflow_root / "dpr_live_state.json")).resolve()

    rows = scan(
        preparation_dir,
        workflow_root=workflow_root,
        staging_dir=staging_dir,
        ingest_records_dir=ingest_records_dir,
        aip_root=aip_root,
    )

    # Point 4: live state is written FIRST and does not depend on Excel being closed.
    write_live_state(state_json, rows)

    # Excel is a presentation snapshot. If Excel has the target workbook locked,
    # keep the live JSON current and write a side snapshot instead.
    excel = args.excel.resolve()
    try:
        write_xlsx(excel, rows)
        excel_result = excel
    except (PermissionError, OSError):
        snapshot = excel.with_name(f"{excel.stem}_latest{excel.suffix}")
        write_xlsx(snapshot, rows)
        excel_result = snapshot
        print(f"[WARN] DPR_Master.xlsx appears to be open/locked; live state was updated.")
        print(f"       Latest Excel snapshot written to: {snapshot}")

    print(f"[OK] DPR live state refreshed: {state_json}")
    print(f"     Excel dashboard: {excel_result}")
    print(f"     JOB_ID rows monitored: {len(rows)}")
    return rows, excel_result


def main() -> int:
    ap = argparse.ArgumentParser(description="DPR Master live-state and Excel workflow monitor")
    ap.add_argument("--preparation-dir", required=True, type=Path)
    ap.add_argument("--staging-dir", type=Path, help="PoC 7 packaging staging root; default: sibling 'staging'")
    ap.add_argument("--ingest-records-dir", type=Path, help="PoC 8/9 record root; default: sibling 'ingest-records'")
    ap.add_argument("--aip-root", type=Path, help="Final OCFL/AIP root; default: sibling 'AIP'")
    ap.add_argument("--excel", required=True, type=Path, help="Output DPR_Master.xlsx")
    ap.add_argument("--state-json", type=Path, help="Live machine-readable state; default: sibling dpr_live_state.json")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="Keep monitor running and refresh every N seconds. 0 = refresh once.")
    args = ap.parse_args()

    if args.watch and args.watch < 2:
        print("[FATAL] --watch must be 0 or at least 2 seconds", file=sys.stderr)
        return 2

    try:
        while True:
            rows, _ = refresh_once(args)
            if not args.watch:
                break
            print(f"[WATCH] Next refresh in {args.watch} seconds. Ctrl+C to stop.")
            time.sleep(args.watch)
        return 0
    except KeyboardInterrupt:
        print("\\n[OK] Live monitor stopped by operator.")
        return 0
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
