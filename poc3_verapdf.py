#!/usr/bin/env python3
"""DOL Digital Preservation PoC 3 - veraPDF validation gate.

Consumes a successful PoC 2 preparation object and, for each created master:
- verifies the master still matches the PoC 2 SHA-512
- invokes veraPDF CLI against the explicit PDF/A-2b profile
- captures the machine-readable XML report in documentation/
- interprets the validation verdict from the XML report
- mints a file_id for the retained passing validation report
- calculates SHA-512 for the report
- records a PoC 3 processing record

Important: veraPDF is the conformance authority. This script does not modify the master.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from workflow_common import load, write_record, verify, coverage
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


class Poc3Error(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha512_file(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def validate_job_id(job_id: str) -> str:
    job_id = (job_id or "").strip()
    if len(job_id) != 11 or job_id[4] != "_" or not job_id[:4].isdigit() or not job_id[5:].isdigit():
        raise Poc3Error(f"Invalid JOB_ID {job_id!r}; expected YYYY_NNNNNN")
    return job_id


def load_poc2_record(obj_dir: Path, job_id: str) -> dict:
    path = obj_dir / "documentation" / f"{job_id}_poc2_conversion_record.json"
    if not path.is_file():
        raise Poc3Error(f"PoC 2 processing record not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "STEP_2_MASTERS_CREATED_UNVALIDATED":
        raise Poc3Error(f"PoC 2 object is not in the expected state: {data.get('status')!r}")
    return data


def resolve_verapdf_cmd(verapdf: Path) -> list[str]:
    """Return the command prefix used to invoke veraPDF.

    On Windows this should normally be the installed verapdf.bat path.
    """
    if verapdf.is_file():
        return [str(verapdf)]
    raise Poc3Error(f"veraPDF launcher not found: {verapdf}")


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_verapdf_xml(report_path: Path) -> dict:
    """Parse enough of veraPDF's machine-readable report to determine verdict.

    veraPDF report schemas have evolved. Rather than bind to one namespace/version,
    search by local element names and known attributes.
    """
    try:
        root = ET.parse(report_path).getroot()
    except ET.ParseError as exc:
        raise Poc3Error(f"veraPDF report is not valid XML: {report_path}: {exc}") from exc

    result = {
        "compliant": None,
        "profile": None,
        "failed_rules": None,
        "passed_rules": None,
        "raw_summary": {},
    }

    for elem in root.iter():
        lname = local_name(elem.tag).lower()
        attrs = {k.split("}", 1)[-1].lower(): v for k, v in elem.attrib.items()}
        text = (elem.text or "").strip()

        # Common veraPDF validation report patterns include isCompliant="true|false".
        for key in ("iscompliant", "compliant"):
            if key in attrs and result["compliant"] is None:
                val = attrs[key].strip().lower()
                if val in {"true", "false"}:
                    result["compliant"] = (val == "true")

        # Some versions may expose a simple element text value.
        if lname in {"iscompliant", "compliant"} and result["compliant"] is None:
            val = text.lower()
            if val in {"true", "false"}:
                result["compliant"] = (val == "true")

        if lname in {"profile", "profile_name", "profilename", "flavour", "flavor"} and text and result["profile"] is None:
            result["profile"] = text
        for key in ("profile", "profilename", "flavour", "flavor"):
            if key in attrs and result["profile"] is None:
                result["profile"] = attrs[key]

        if lname in {"validationreport", "validation_report"}:
            for key in ("failedchecks", "failedrules", "failed"):
                if key in attrs:
                    try:
                        result["failed_rules"] = int(attrs[key])
                    except ValueError:
                        pass
            for key in ("passedchecks", "passedrules", "passed"):
                if key in attrs:
                    try:
                        result["passed_rules"] = int(attrs[key])
                    except ValueError:
                        pass


    if result["compliant"] is None:
        raise Poc3Error(
            "Could not determine veraPDF conformance verdict from the XML report. "
            f"Inspect: {report_path}"
        )

    return result


def run_verapdf(*, verapdf: Path, master: Path, report_path: Path, flavour: str) -> tuple[dict, str, str, int]:
    cmd = resolve_verapdf_cmd(verapdf)
    # veraPDF CLI: -f / --flavour selects a profile, --format xml emits the
    # machine-readable XML report to stdout. Explicit flavour avoids relying on
    # auto-detection or the document's own declaration.
    full_cmd = cmd + ["--flavour", flavour, "--format", "xml", str(master)]
    result = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )

    stdout = result.stdout
    stderr = result.stderr

    # veraPDF writes the report to stdout. Preserve it exactly as tool evidence.
    if stdout.strip():
        report_path.write_text(stdout, encoding="utf-8")
    else:
        raise Poc3Error(
            f"veraPDF produced no XML report for {master.name} (exit {result.returncode}).\n"
            f"STDERR:\n{stderr.strip()}"
        )

    parsed = parse_verapdf_xml(report_path)
    return parsed, stdout, stderr, result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="DOL Digital Preservation PoC 3 - veraPDF PDF/A-2b validation gate")
    parser.add_argument("--preparation-dir", required=True, type=Path, help="PoC preparation root")
    parser.add_argument("--job-id", required=True, help="Canonical JOB_ID, e.g. 2025_003033")
    parser.add_argument("--verapdf", required=True, type=Path, help="Path to veraPDF launcher, normally verapdf.bat on Windows")
    parser.add_argument("--flavour", default="2b", choices=["1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u", "4", "4f", "4e"], help="veraPDF validation flavour; PoC 3 default is 2b")
    args = parser.parse_args()

    try:
        job_id = validate_job_id(args.job_id)
        obj_dir = args.preparation_dir / job_id
        if not obj_dir.is_dir():
            raise Poc3Error(f"Prepared JOB_ID folder not found: {obj_dir}")

        poc2 = load_poc2_record(obj_dir, job_id)
        coverage(obj_dir, {2: poc2})
        conversions = poc2.get("conversions") or []
        if not conversions:
            raise Poc3Error("PoC 2 record contains no conversions to validate")

        documentation = obj_dir / "documentation"
        documentation.mkdir(parents=True, exist_ok=True)

        record_path = documentation / f"{job_id}_poc3_validation_record.json"
        validations = load(record_path).get("validations", [])
        pass_count = 0
        fail_count = 0

        for conv in conversions:
            master = Path(conv["master_path"])
            if not master.is_file():
                raise Poc3Error(f"Master from PoC 2 record no longer exists: {master}")

            expected_master_sha512 = conv.get("master_sha512")
            current_master_sha512 = sha512_file(master)
            if expected_master_sha512 and current_master_sha512.lower() != expected_master_sha512.lower():
                raise Poc3Error(
                    f"Master fixity changed since PoC 2: {master.name}\n"
                    f"PoC 2: {expected_master_sha512}\nCurrent: {current_master_sha512}"
                )

            previous = next((v for v in validations if v.get("master_file_id") == conv["master_file_id"]), None)
            if previous:
                verify(master, previous["master_sha512"])
                verify(previous["validation_report_path"], previous["validation_report_sha512"])
                if previous.get("requested_flavour") != args.flavour:
                    raise Poc3Error("Existing validation uses another flavour; explicit review required")
                pass_count += previous.get("verdict") == "PASS"
                fail_count += previous.get("verdict") != "PASS"
                continue
            base = master.stem
            report_path = documentation / f"{base}_val_verapdf.xml"
            driver_log = documentation / f"{base}_val_driver.txt"
            if report_path.exists() or driver_log.exists():
                raise Poc3Error(f"Refusing to overwrite existing validation evidence for {base}")

            parsed, stdout, stderr, exit_code = run_verapdf(
                verapdf=args.verapdf,
                master=master,
                report_path=report_path,
                flavour=args.flavour,
            )

            compliant = bool(parsed["compliant"]) and exit_code == 0
            outcome = "PASS" if compliant else "FAIL"

            driver_log.write_text(
                "DPR PoC 3 veraPDF invocation\n"
                f"timestamp_utc={utc_now()}\n"
                f"master={master.resolve()}\n"
                f"master_sha512={current_master_sha512}\n"
                f"requested_flavour={args.flavour}\n"
                f"verdict={outcome}\n"
                f"verapdf_exit_code={exit_code}\n\n"
                f"STDERR\n{stderr}\n",
                encoding="utf-8",
            )

            report_sha512 = sha512_file(report_path)
            report_file_id = str(uuid.uuid4()) if compliant else None

            validations.append({
                "master_file_id": conv.get("master_file_id"),
                "master_path": str(master.resolve()),
                "master_sha512_verified_against_poc2": True,
                "master_sha512": current_master_sha512,
                "requested_flavour": args.flavour,
                "target_claimed_from_poc2": conv.get("target_claimed"),
                "verdict": outcome,
                "conformance_validated": compliant,
                "verapdf_exit_code": exit_code,
                "verapdf_profile_reported": parsed.get("profile"),
                "failed_rules": parsed.get("failed_rules"),
                "passed_rules": parsed.get("passed_rules"),
                "validation_report_path": str(report_path.resolve()),
                "validation_report_file_id": report_file_id,
                "validation_report_sha512": report_sha512,
                "driver_log": str(driver_log.resolve()),
                "relationship": {
                    "type": "isValidationReportOf" if compliant else None,
                    "report_file_id": report_file_id,
                    "master_file_id": conv.get("master_file_id"),
                },
            })

            write_record(record_path, {"job_id": job_id, "status": "STEP_3_IN_PROGRESS", "validations": validations})
            if compliant:
                pass_count += 1
                print(f"[PASS] {master.name} conforms to PDF/A-{args.flavour}")
                print(f"       master file_id: {conv.get('master_file_id')}")
                print(f"       report file_id: {report_file_id}")
                print(f"       report: {report_path}")
            else:
                fail_count += 1
                print(f"[FAIL] {master.name} does NOT conform to PDF/A-{args.flavour}")
                print(f"       report retained for PoC diagnosis: {report_path}")
                print("       gate: HOLD / DIVERT FOR REVIEW")

        record_status = "STEP_3_VERAPDF_PASSED" if fail_count == 0 else "STEP_3_VERAPDF_FAILED_HOLD"
        record = {
            "job_id": job_id,
            "folder_uuid": poc2.get("folder_uuid"),
            "created_at": utc_now(),
            "status": record_status,
            "authority": "veraPDF",
            "requested_flavour": args.flavour,
            "validation_count": len(validations),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "validations": validations,
            "note": (
                "PoC processing record only; not the future pmsystem event store. "
                "The master is never modified by PoC 3. A PASS may proceed to PoC 4 QA; "
                "a FAIL is a gate and must be held/diverted for review."
            ),
        }
        record_path = documentation / f"{job_id}_poc3_validation_record.json"
        write_record(record_path, record)

        print(f"\nCompleted: {pass_count} passed, {fail_count} failed")
        print(f"PoC 3 record: {record_path}")
        if fail_count:
            return 10
        return 0

    except FileNotFoundError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    except Poc3Error as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[FATAL] Unexpected error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
