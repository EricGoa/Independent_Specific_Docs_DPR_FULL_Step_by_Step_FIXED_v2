#!/usr/bin/env python3
"""
PoC 8 — independent ingest-worker BagIt verification.

Purpose:
  Treat the staged BagIt SIP as UNTRUSTED input and independently verify it
  before any OCFL ingest is allowed.

Inputs:
  <staging>/<JOB_ID>/

Outputs:
  <ingest-records>/<JOB_ID>_poc8_ingest_verification_record.json

On success:
  - bag remains in staging
  - status = STEP_8_PACKAGE_FIXITY_VERIFIED

On failure:
  - bag is moved to <held>/<JOB_ID> unless --no-move-on-fail is set
  - status = STEP_8_PACKAGE_HELD_FOR_REVIEW

This script does not use the PoC 7 record as a source of truth.
It verifies the bag from its own contents:
  bagit.txt
  bag-info.txt
  manifest-sha512.txt
  tagmanifest-sha512.txt
  data/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from workflow_common import write_record, safe_relative, working_pdf
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )


def parse_tag_file(path: Path):
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        values.setdefault(k.strip(), []).append(v.strip())
    return values


def one(tags, name, default=None):
    vals = tags.get(name, [])
    return vals[0] if vals else default


def parse_manifest_line(line: str):
    line = line.rstrip("\r\n")
    if not line:
        return None
    parts = line.split(None, 1)
    if len(parts) != 2:
        raise ValueError(f"Malformed manifest line: {line!r}")
    return parts[0].strip(), parts[1].strip()


def validate_manifest(manifest_path: Path, bag_root: Path):
    results = []
    listed = set()
    errors = []

    if not manifest_path.is_file():
        return results, listed, [f"missing manifest: {manifest_path.name}"]

    for line_no, raw in enumerate(
        manifest_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(),
        start=1
    ):
        if not raw.strip():
            continue
        try:
            expected, relp = parse_manifest_line(raw)
        except Exception as e:
            errors.append(f"{manifest_path.name}:{line_no}: {e}")
            continue

        normalized = relp.replace("\\", "/")
        if normalized in listed:
            errors.append(f"duplicate manifest path: {normalized}")
        listed.add(normalized)
        try:
            fs_path = safe_relative(bag_root, normalized)
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            continue

        item = {
            "path": normalized,
            "expected_sha512": expected,
            "exists": fs_path.is_file(),
            "actual_sha512": None,
            "verified": False,
        }

        if not fs_path.is_file():
            errors.append(f"missing file referenced by {manifest_path.name}: {normalized}")
            results.append(item)
            continue

        actual = sha512(fs_path)
        item["actual_sha512"] = actual
        item["verified"] = actual.lower() == expected.lower()

        if not item["verified"]:
            errors.append(
                f"checksum mismatch: {normalized}: expected {expected}, got {actual}"
            )

        results.append(item)

    return results, listed, errors


def compute_payload_oxum(bag_root: Path):
    files = [p for p in (bag_root / "data").rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return total, len(files)


def parse_oxum(value: str):
    if not value or "." not in value:
        raise ValueError(f"Invalid Payload-Oxum: {value!r}")
    octets, count = value.rsplit(".", 1)
    return int(octets), int(count)


def validate_bag_structure(bag_root: Path, expected_job_id: str):
    errors = []
    warnings = []

    required_files = [
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha512.txt",
        "tagmanifest-sha512.txt",
    ]
    for name in required_files:
        if not (bag_root / name).is_file():
            errors.append(f"missing required BagIt file: {name}")

    if not (bag_root / "data").is_dir():
        errors.append("missing required payload directory: data/")

    if errors:
        return {
            "errors": errors,
            "warnings": warnings,
            "bagit": {},
            "bag_info": {},
        }

    bagit = parse_tag_file(bag_root / "bagit.txt")
    info = parse_tag_file(bag_root / "bag-info.txt")

    if one(bagit, "BagIt-Version") != "0.97":
        warnings.append(
            f"BagIt-Version is {one(bagit, 'BagIt-Version')!r}, expected PoC value '0.97'"
        )

    if one(info,"DPR-Object-UUID"):
        if one(info,"Internal-Sender-Identifier") != expected_job_id: errors.append("Internal-Sender-Identifier/JOB_ID mismatch")
        return {"errors":errors,"warnings":warnings,"bagit":bagit,"bag_info":info}

    ext_id = one(info, "External-Identifier")
    if ext_id != expected_job_id:
        errors.append(
            f"External-Identifier mismatch: expected {expected_job_id}, got {ext_id}"
        )

    sender_id = one(info, "Internal-Sender-Identifier")
    if not sender_id:
        errors.append("Internal-Sender-Identifier missing")
    else:
        try:
            uuid.UUID(sender_id)
        except Exception:
            errors.append(
                f"Internal-Sender-Identifier is not a UUID: {sender_id}"
            )

    return {
        "errors": errors,
        "warnings": warnings,
        "bagit": bagit,
        "bag_info": info,
    }


def policy_checks(bag_root: Path, job_id: str):
    """
    Workflow-level checks beyond raw BagIt syntax.
    """
    errors = []
    warnings = []

    payload_files = [
        p.relative_to(bag_root).as_posix()
        for p in (bag_root / "data").rglob("*")
        if p.is_file()
    ]

    srcs = [x for x in payload_files if working_pdf(x)]
    if srcs:
        errors.append(
            "transient _src working file(s) present in staged SIP: " + ", ".join(srcs)
        )

    modern = (bag_root / "data" / f"{job_id}_mets.xml").exists()
    required_metadata = ([f"data/metadata/descriptive/{job_id}_dome.xml", f"data/metadata/preservation/{job_id}_premis.xml", f"data/{job_id}_mets.xml"] if modern else [
        f"data/metadata/{job_id}_dome.xml",
        f"data/metadata/{job_id}_premis.xml",
        f"data/metadata/{job_id}_mets.xml",
    ])
    for rp in required_metadata:
        if rp not in payload_files:
            errors.append(f"required Step-6 metadata absent from payload: {rp}")

    return errors, warnings, payload_files


def move_to_hold(bag_root: Path, held_dir: Path, job_id: str):
    held_dir.mkdir(parents=True, exist_ok=True)
    target = held_dir / job_id

    if target.exists():
        suffix = uuid.uuid4().hex
        target = held_dir / f"{job_id}_{suffix}"

    shutil.move(str(bag_root), str(target))
    return target


def main():
    ap = argparse.ArgumentParser(
        description="PoC 8: independent ingest-worker BagIt verification"
    )
    ap.add_argument("--staging-dir", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--aip-root",type=Path)
    ap.add_argument("--agent-registry",type=Path)
    ap.add_argument("--allow-legacy-package",action="store_true",help="Explicit compatibility mode; legacy identity rules")
    ap.add_argument(
        "--held-dir",
        help="Directory for failed bags. Default: sibling 'held' directory."
    )
    ap.add_argument(
        "--ingest-records-dir",
        help="Directory for PoC 8 verification records. Default: sibling 'ingest-records'."
    )
    ap.add_argument(
        "--no-move-on-fail",
        action="store_true",
        help="Leave an invalid bag in staging but still mark it HELD_FOR_REVIEW."
    )
    args = ap.parse_args()

    staging = Path(args.staging_dir).resolve()
    bag_root = staging / args.job_id

    parent = staging.parent
    held_dir = Path(args.held_dir).resolve() if args.held_dir else parent / "held"
    records_dir = (
        Path(args.ingest_records_dir).resolve()
        if args.ingest_records_dir
        else parent / "ingest-records"
    )
    record_path = records_dir / f"{args.job_id}_poc8_ingest_verification_record.json"

    started = utc_now()

    print("[INFO] dpr_dol_ingest_worker PoC 8")
    print(f"       staged bag: {bag_root}")
    print("       trust model: UNTRUSTED — verifying from bag contents only")

    if not bag_root.is_dir():
        print(f"[FATAL] staged bag not found: {bag_root}", file=sys.stderr)
        return 2

    structural = validate_bag_structure(bag_root, args.job_id)

    payload_results, payload_listed, payload_errors = validate_manifest(
        bag_root / "manifest-sha512.txt",
        bag_root
    )
    tag_results, tag_listed, tag_errors = validate_manifest(
        bag_root / "tagmanifest-sha512.txt",
        bag_root
    )

    errors = list(structural["errors"]) + payload_errors + tag_errors
    warnings = list(structural["warnings"])

    actual_payload = {
        p.relative_to(bag_root).as_posix()
        for p in (bag_root / "data").rglob("*")
        if p.is_file()
    }

    for p in sorted(actual_payload - payload_listed):
        errors.append(f"payload file absent from manifest-sha512.txt: {p}")
    for p in sorted(payload_listed - actual_payload):
        errors.append(f"manifest-sha512.txt references non-payload file: {p}")

    required_tags = {"bagit.txt", "bag-info.txt", "manifest-sha512.txt"}
    for p in sorted(required_tags - tag_listed):
        errors.append(f"required tag absent from tagmanifest-sha512.txt: {p}")

    # Payload-Oxum is an independent completeness check before/alongside hashing.
    actual_octets, actual_count = compute_payload_oxum(bag_root)
    oxum_text = one(structural["bag_info"], "Payload-Oxum")
    oxum_check = {
        "declared": oxum_text,
        "actual": f"{actual_octets}.{actual_count}",
        "verified": False,
    }

    if oxum_text:
        try:
            declared_octets, declared_count = parse_oxum(oxum_text)
            oxum_check["verified"] = (
                declared_octets == actual_octets and
                declared_count == actual_count
            )
            if not oxum_check["verified"]:
                errors.append(
                    f"Payload-Oxum mismatch: declared {oxum_text}, "
                    f"actual {actual_octets}.{actual_count}"
                )
        except Exception as e:
            errors.append(str(e))
    else:
        errors.append("Payload-Oxum missing from bag-info.txt")

    policy_errors, policy_warnings, payload_files = policy_checks(
        bag_root, args.job_id
    )
    errors.extend(policy_errors)
    warnings.extend(policy_warnings)

    modern = one(structural["bag_info"],"DPR-Object-UUID") is not None
    if modern:
        try:
            from profile_workflow import check_contract
            check_contract(bag_root,args.job_id,args.aip_root or staging.parent/"AIP")
        except Exception as exc:
            errors.append(str(exc))
    elif not args.allow_legacy_package:
        errors.append("Legacy bag identity scheme requires --allow-legacy-package; rebuild a new profile SIP for normal ingest")
    metadata_snapshot = None
    if modern and not errors:
        try:
            from profile_workflow import step8_metadata
            metadata_snapshot = step8_metadata(bag_root,args.job_id,records_dir,
                args.aip_root or staging.parent/'AIP',args.agent_registry)
        except Exception as exc:
            errors.append('Step 8 metadata update failed: '+str(exc))
    passed = len(errors) == 0

    record = {
        "poc": 8,
        "step8_metadata": metadata_snapshot,
        "job_id": args.job_id,
        "started_utc": started,
        "completed_utc": utc_now(),
        "actor": "dpr_dol_ingest_worker (PoC)",
        "status": (
            "STEP_8_PACKAGE_FIXITY_VERIFIED"
            if passed
            else "STEP_8_PACKAGE_HELD_FOR_REVIEW"
        ),
        "event_projection": {
            "event": "PackageFixityVerifiedOnIngest",
            "event_type": "fixity check",
            "outcome": "verified" if passed else "failed",
        },
        "trust_model": (
            "Bag verified independently from bag contents; "
            "PoC 7 producer record was not used as authority."
        ),
        "bag_path_at_start": str(bag_root),
        "sip_uuid": one(structural["bag_info"],"External-Identifier") if modern else None,
        "object_uuid": one(structural["bag_info"],"DPR-Object-UUID"),
        "external_identifier": one(structural["bag_info"], "External-Identifier"),
        "internal_sender_identifier": one(
            structural["bag_info"], "Internal-Sender-Identifier"
        ),
        "bag_group_identifier": one(
            structural["bag_info"], "Bag-Group-Identifier"
        ),
        "payload_oxum": oxum_check,
        "payload_manifest": {
            "algorithm": "SHA-512",
            "listed_file_count": len(payload_listed),
            "verified_file_count": sum(1 for x in payload_results if x["verified"]),
            "entries": payload_results,
        },
        "tagmanifest": {
            "algorithm": "SHA-512",
            "listed_file_count": len(tag_listed),
            "verified_file_count": sum(1 for x in tag_results if x["verified"]),
            "entries": tag_results,
        },
        "payload_files_seen": sorted(payload_files),
        "warnings": warnings,
        "errors": errors,
        "disposition": "READY_FOR_OCFL_INGEST" if passed else "HELD_FOR_REVIEW",
        "next_gate": (
            "PoC 9 — OCFL 1.1 object construction with verify-on-write"
            if passed
            else "Human investigation / repair, then re-run PoC 8"
        ),
    }

    if passed:
        record["bag_path_after_verification"] = str(bag_root)
        if modern:
            write_record(records_dir / f"{args.job_id}_{record['sip_uuid']}_poc8_ingest_verification_record.json", record)
        write_record(record_path, record)

        print()
        print("[PASS] PackageFixityVerifiedOnIngest")
        print("       outcome: verified")
        print(f"       payload files verified: {len(payload_listed)}")
        print(f"       tag files verified:     {len(tag_listed)}")
        print(f"       Payload-Oxum:           {actual_octets}.{actual_count} VERIFIED")
        print(f"       disposition:            READY_FOR_OCFL_INGEST")
        print(f"       record:                 {record_path}")
        print("Status: STEP_8_PACKAGE_FIXITY_VERIFIED")
        return 0

    # Failure path.
    held_path = None
    if not args.no_move_on_fail:
        try:
            held_path = move_to_hold(bag_root, held_dir, args.job_id)
            record["bag_path_after_verification"] = str(held_path)
        except Exception as move_error:
            errors.append(f"failed to move invalid bag to held area: {move_error}")
            record["bag_path_after_verification"] = str(bag_root)
    else:
        record["bag_path_after_verification"] = str(bag_root)

    write_record(record_path, record)

    print()
    print("[FAIL] PackageFixityVerifiedOnIngest")
    print("       outcome: failed")
    print("       disposition: HELD_FOR_REVIEW")
    for e in errors:
        print("       ERROR:", e)
    if held_path:
        print(f"       bag moved to: {held_path}")
    print(f"       record: {record_path}")
    print("Status: STEP_8_PACKAGE_HELD_FOR_REVIEW")
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
