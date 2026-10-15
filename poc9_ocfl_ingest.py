#!/usr/bin/env python3
"""
PoC 9 — OCFL 1.1 AIP construction with verify-on-write.

Creates v1 or appends a forward-delta version from a BagIt SIP verified by
PoC8. Previous version directories and logical paths are retained. Identical
logical states do not create another version. New content is verified on write;
all historical inventories, sidecars and stored content are checked before commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from workflow_common import write_record, safe_relative
from ocfl_versions import validate as validate_versions, build_delta, commit_delta
from datetime import datetime, timezone
from pathlib import Path


OCFL_VERSION = "1.1"
DIGEST_ALGORITHM = "sha512"
INVENTORY_TYPE = "https://ocfl.io/1.1/spec/#inventory"


def utc_now_seconds():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    path.write_text(data, encoding="utf-8", newline="\n")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_tag_file(path: Path):
    result = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        result.setdefault(k.strip(), []).append(v.strip())
    return result


def one(tags, key, default=None):
    vals = tags.get(key, [])
    return vals[0] if vals else default


def parse_manifest(path: Path):
    entries = {}
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1
    ):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise RuntimeError(f"Malformed manifest line {line_no}: {line!r}")
        digest, relp = parts
        relp = relp.strip().replace("\\", "/")
        if relp in entries:
            raise RuntimeError(f"Duplicate path in BagIt manifest: {relp}")
        entries[relp] = digest.lower()
    return entries


def job_storage_parts(job_id: str):
    """
    2025_003033 -> ("2025", "003000")
    """
    try:
        year, seq = job_id.split("_", 1)
        if len(year) != 4 or not year.isdigit():
            raise ValueError
        if not seq.isdigit():
            raise ValueError
        n = int(seq)
    except Exception:
        raise RuntimeError(
            f"JOB_ID must be YYYY_NNNNNN-like for this PoC; got {job_id!r}"
        )
    thousands = (n // 1000) * 1000
    return year, f"{thousands:06d}"


def ensure_storage_root(aip_root: Path):
    aip_root.mkdir(parents=True, exist_ok=True)
    decl = aip_root / "0=ocfl_1.1"

    # Refuse conflicting OCFL declarations.
    others = list(aip_root.glob("0=ocfl_*"))
    for p in others:
        if p.name != "0=ocfl_1.1":
            raise RuntimeError(
                f"Conflicting OCFL storage-root declaration exists: {p}"
            )

    expected = "ocfl_1.1\n"
    if decl.exists():
        actual = decl.read_text(encoding="utf-8", errors="replace")
        if actual != expected:
            raise RuntimeError(
                f"Invalid existing storage-root declaration: {decl}"
            )
    else:
        decl.write_text(expected, encoding="utf-8", newline="\n")


def verify_poc8(record_path: Path, job_id: str):
    if not record_path.is_file():
        raise RuntimeError(f"PoC 8 verification record not found: {record_path}")
    r = load_json(record_path)
    if r.get("job_id") != job_id:
        raise RuntimeError(
            f"PoC 8 JOB_ID mismatch: {r.get('job_id')} != {job_id}"
        )
    if r.get("status") != "STEP_8_PACKAGE_FIXITY_VERIFIED":
        raise RuntimeError(
            "PoC 8 has not verified this package. Status: "
            + str(r.get("status"))
        )
    if r.get("disposition") != "READY_FOR_OCFL_INGEST":
        raise RuntimeError(
            "PoC 8 disposition is not READY_FOR_OCFL_INGEST: "
            + str(r.get("disposition"))
        )
    return r


def verify_bag_again(bag_root: Path, job_id: str):
    """
    PoC 9 does not merely trust PoC 8. It performs the minimum receipt check
    needed to establish the exact BagIt manifest used for verify-on-write.
    """
    manifest = bag_root / "manifest-sha512.txt"
    info_path = bag_root / "bag-info.txt"
    data = bag_root / "data"

    if not manifest.is_file() or not info_path.is_file() or not data.is_dir():
        raise RuntimeError("Staged bag is missing manifest, bag-info, or data/")

    info = parse_tag_file(info_path)
    if one(info, "External-Identifier") != job_id:
        raise RuntimeError("Bag External-Identifier does not match JOB_ID")

    objid = one(info, "Internal-Sender-Identifier")
    if not objid:
        raise RuntimeError("Bag Internal-Sender-Identifier is missing")
    try:
        uuid.UUID(objid)
    except Exception:
        raise RuntimeError(
            f"Internal-Sender-Identifier is not a UUID: {objid}"
        )

    entries = parse_manifest(manifest)
    actual_payload = {
        p.relative_to(bag_root).as_posix()
        for p in data.rglob("*")
        if p.is_file()
    }

    if set(entries) != actual_payload:
        missing = sorted(actual_payload - set(entries))
        extra = sorted(set(entries) - actual_payload)
        raise RuntimeError(
            "Bag payload/manifest mismatch. "
            f"missing_from_manifest={missing}, extra_in_manifest={extra}"
        )

    for relp, expected in entries.items():
        p = safe_relative(bag_root, relp)
        actual = sha512(p)
        if actual != expected:
            raise RuntimeError(
                f"Bag changed after PoC 8: checksum mismatch for {relp}"
            )

    return info, entries, objid


def logical_from_bag_path(bag_manifest_path: str):
    if not bag_manifest_path.startswith("data/"):
        raise RuntimeError(
            f"Bag payload path does not begin data/: {bag_manifest_path}"
        )
    logical = bag_manifest_path[len("data/"):]
    if not logical or logical.startswith("/") or logical.endswith("/"):
        raise RuntimeError(f"Unsafe logical path: {logical!r}")
    parts = logical.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise RuntimeError(f"Unsafe logical path: {logical!r}")
    return logical


def build_v1_workspace(
    bag_root: Path,
    bag_entries: dict[str, str],
    objid: str,
    workspace_object: Path,
    user_name: str,
    user_address: str | None,
):
    if workspace_object.exists():
        raise RuntimeError(f"Workspace already exists: {workspace_object}")
    content = workspace_object / "v1" / "content"
    content.mkdir(parents=True)

    manifest = {}
    state = {}
    copy_results = []

    for bag_path, expected_digest in sorted(bag_entries.items()):
        logical = logical_from_bag_path(bag_path)
        src = bag_root / Path(bag_path.replace("/", os.sep))
        dst = content / Path(logical.replace("/", os.sep))
        dst.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(src, dst)

        # Verify-on-write against the checksum struck in the BagIt manifest.
        actual = sha512(dst)
        if actual != expected_digest:
            raise RuntimeError(
                f"VERIFY-ON-WRITE FAILED for {logical}: "
                f"BagIt={expected_digest}, OCFL-copy={actual}"
            )

        physical = f"v1/content/{logical}"
        manifest.setdefault(actual, []).append(physical)
        state.setdefault(actual, []).append(logical)

        copy_results.append({
            "logical_path": logical,
            "bag_manifest_sha512": expected_digest,
            "ocfl_written_sha512": actual,
            "verified": True,
            "physical_path": physical,
            "size_bytes": dst.stat().st_size,
        })

    version = {
        "created": utc_now_seconds(),
        "message": "Initial preservation ingest from verified BagIt SIP",
        "state": {k: sorted(v) for k, v in sorted(state.items())},
        "user": {"name": user_name},
    }
    if user_address:
        version["user"]["address"] = user_address

    inventory = {
        "id": objid,
        "type": INVENTORY_TYPE,
        "digestAlgorithm": DIGEST_ALGORITHM,
        "head": "v1",
        "manifest": {k: sorted(v) for k, v in sorted(manifest.items())},
        "versions": {
            "v1": version
        },
    }

    # Object conformance declaration.
    (workspace_object / "0=ocfl_object_1.1").write_text(
        "ocfl_object_1.1\n",
        encoding="utf-8",
        newline="\n",
    )

    # Write exactly the same inventory bytes at root and v1.
    root_inventory = workspace_object / "inventory.json"
    version_inventory = workspace_object / "v1" / "inventory.json"

    payload = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    root_inventory.write_text(payload, encoding="utf-8", newline="\n")
    version_inventory.write_text(payload, encoding="utf-8", newline="\n")

    inv_digest = sha512(root_inventory)
    if sha512(version_inventory) != inv_digest:
        raise RuntimeError("Root and v1 inventory files are not byte-identical")

    sidecar_text = f"{inv_digest} inventory.json\n"
    (workspace_object / "inventory.json.sha512").write_text(
        sidecar_text, encoding="utf-8", newline="\n"
    )
    (workspace_object / "v1" / "inventory.json.sha512").write_text(
        sidecar_text, encoding="utf-8", newline="\n"
    )

    return inventory, copy_results


def validate_ocfl_object(object_root: Path, expected_objid: str):
    return validate_versions(object_root, expected_objid)


def directory_snapshot(root: Path):
    result = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            result.append({
                "path": p.relative_to(root).as_posix(),
                "size_bytes": p.stat().st_size,
                "sha512": sha512(p),
            })
    return result


def main():
    ap = argparse.ArgumentParser(
        description="PoC 9: append OCFL 1.1 AIP versions with verify-on-write"
    )
    ap.add_argument("--staging-dir", required=True)
    ap.add_argument("--aip-root", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--agent-registry",type=Path)
    ap.add_argument("--description")
    ap.add_argument("--ocfl-validator",type=Path)
    ap.add_argument("--validator-python",type=Path)
    ap.add_argument("--strict-profile",action="store_true")
    ap.add_argument("--require-independent-validator",action="store_true")
    ap.add_argument("--allow-legacy-package",action="store_true")
    ap.add_argument(
        "--ingest-records-dir",
        help="Default: sibling ingest-records directory beside staging"
    )
    ap.add_argument(
        "--workspace-dir",
        help="Default: sibling ocfl-workspace directory beside AIP root"
    )
    ap.add_argument(
        "--user-name",
        default=None,
        help="OCFL version user.name"
    )
    ap.add_argument(
        "--user-address",
        help="Optional URI, e.g. mailto:eric@example.org"
    )
    args = ap.parse_args()

    staging = Path(args.staging_dir).resolve()
    aip_root = Path(args.aip_root).resolve()
    bag_root = staging / args.job_id

    records_dir = (
        Path(args.ingest_records_dir).resolve()
        if args.ingest_records_dir
        else staging.parent / "ingest-records"
    )
    p8_record = records_dir / f"{args.job_id}_poc8_ingest_verification_record.json"
    p9_record = records_dir / f"{args.job_id}_poc9_ocfl_ingest_record.json"

    workspace_root = (
        Path(args.workspace_dir).resolve()
        if args.workspace_dir
        else aip_root.parent / "ocfl-workspace"
    )

    started = utc_now_seconds()

    try:
        print("[INFO] dpr_dol_ingest_worker PoC 9 — OCFL 1.1")
        if bag_root.is_dir():
            from profile_workflow import tags,ingest
            if tags(bag_root).get("DPR-Object-UUID"):
                return ingest(args,bag_root)
        if not args.allow_legacy_package:
            raise RuntimeError("Legacy bag identity requires explicit --allow-legacy-package")
        p8 = verify_poc8(p8_record, args.job_id)
        print("[INFO] PoC 8 gate: VERIFIED")

        if not bag_root.is_dir():
            raise RuntimeError(f"Verified staging bag not found: {bag_root}")

        bag_info, bag_entries, objid = verify_bag_again(bag_root, args.job_id)
        verified_entries = {e["path"]: e["actual_sha512"] for e in p8.get("payload_manifest", {}).get("entries", []) if e.get("verified")}
        if verified_entries != bag_entries or p8.get("internal_sender_identifier") != objid:
            raise RuntimeError("Current bag differs from the exact package verified by PoC8")
        print(f"[INFO] Staged bag rechecked: {len(bag_entries)} payload file(s)")
        print(f"       OCFL OBJID: {objid}")

        ensure_storage_root(aip_root)

        year, thousands = job_storage_parts(args.job_id)
        final_object = aip_root / year / thousands / args.job_id

        existing = final_object.exists()
        previous_head = load_json(final_object / "inventory.json")["head"] if existing else None
        workspace_object = workspace_root / year / thousands / (args.job_id + "_" + uuid.uuid4().hex)
        workspace_object.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            inventory, copies = build_delta(bag_root, bag_entries, objid, final_object, workspace_object, args.user_name, args.user_address, utc_now_seconds())
            if not workspace_object.exists():
                print("[REUSE] Identical logical state; no new OCFL version")
                return 0
        else:
            inventory, copies = build_v1_workspace(bag_root, bag_entries, objid, workspace_object, args.user_name, args.user_address)

        print(f"[INFO] Verify-on-write passed for {len(copies)} file(s)")

        errors = validate_ocfl_object(workspace_object, objid)
        if errors:
            raise RuntimeError(
                "OCFL object validation failed before commit:\n  "
                + "\n  ".join(errors)
            )

        print("[INFO] OCFL pre-commit validation: PASS")

        # Commit: move finished object into its deterministic final path.
        final_object.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            commit_delta(workspace_object, final_object, previous_head)
        else:
            if final_object.exists(): raise RuntimeError("OCFL object appeared during ingest")
            workspace_object.rename(final_object)

        # Revalidate after the move to ensure commit did not change bits.
        post_errors = validate_ocfl_object(final_object, objid)
        if post_errors:
            raise RuntimeError(
                "OCFL post-commit validation failed:\n  "
                + "\n  ".join(post_errors)
            )

        # Verify the bag's expected digests resolve identically inside OCFL state.
        state = inventory["versions"][inventory["head"]]["state"]
        bag_digest_set = set(bag_entries.values())
        state_digest_set = set(state.keys())
        if not bag_digest_set <= state_digest_set:
            raise RuntimeError(
                "BagIt digest set is not represented in the OCFL head state after commit"
            )

        completed = utc_now_seconds()
        record = {
            "poc": 9,
            "job_id": args.job_id,
            "started_utc": started,
            "completed_utc": completed,
            "actor": "dpr_dol_ingest_worker (PoC)",
            "status": "STEP_9_OCFL_AIP_COMMITTED",
            "selection_basis": "incremental_arrival" if existing else "new_document",
            "ocfl": {
                "specification": "1.1",
                "storage_root": str(aip_root),
                "object_path": str(final_object),
                "object_id": objid,
                "head": inventory["head"],
                "digest_algorithm": "sha512",
                "storage_mapping": {
                    "year": year,
                    "thousands": thousands,
                    "job_id": args.job_id,
                },
            },
            "verify_on_write": {
                "outcome": "PASS",
                "bag_payload_file_count": len(bag_entries),
                "ocfl_written_file_count": len(copies),
                "files": copies,
            },
            "ocfl_validation": {
                "pre_commit": "PASS",
                "post_commit": "PASS",
                "focused_validator": (
                    "PoC validator covering declarations, inventory/sidecars, "
                    "manifest/state consistency, physical content and SHA-512."
                ),
            },
            "staging_package": {
                "path": str(bag_root),
                "retained_after_commit": True,
                "note": (
                    "Workflow buffer-window behavior: successful staging bag "
                    "is retained temporarily and is not purged by PoC 9."
                ),
            },
            "event_projection": [
                {
                    "event": "ArchivalPackageCreated",
                    "eventType": "information package creation",
                    "outcome": "success",
                },
                {
                    "event": "DocumentVersionSealed",
                    "eventType": "ingestion end",
                    "version": inventory["head"],
                    "outcome": "success",
                },
                {
                    "event": "Accessioned",
                    "eventType": "accession",
                    "version": inventory["head"],
                    "outcome": "success",
                },
            ],
            "object_snapshot": directory_snapshot(final_object),
            "disposition": "AIP_PRESERVED_OCFL_" + inventory["head"].upper(),
            "next_step": (
                "Retain staging under the approved retention policy; operationalize the event store."
            ),
        }
        write_record(p9_record, record)

        print()
        print("[PASS] OCFL 1.1 AIP committed")
        print(f"       object path:    {final_object}")
        print(f"       OBJID:          {objid}")
        print(f"       version sealed: {inventory['head']}")
        print(f"       content files:  {len(copies)}")
        print("       verify-on-write: PASS")
        print("       post-commit validation: PASS")
        print(f"       staging bag retained: {bag_root}")
        print(f"       record:         {p9_record}")
        print("Status: STEP_9_OCFL_AIP_COMMITTED")
        return 0

    except Exception as e:
        # Never remove staging bag on failure.
        failure_record = {
            "poc": 9,
            "job_id": args.job_id,
            "started_utc": started,
            "completed_utc": utc_now_seconds(),
            "status": "STEP_9_OCFL_INGEST_FAILED",
            "error": str(e),
            "staging_bag_preserved": str(bag_root),
            "held": True,
            "disposition": "HOLD_FOR_REVIEW_AND_RETRY",
        }
        try:
            write_record(p9_record, failure_record)
        except Exception:
            pass
        print(f"[FATAL] {e}", file=sys.stderr)
        print("Status: STEP_9_OCFL_INGEST_FAILED", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
