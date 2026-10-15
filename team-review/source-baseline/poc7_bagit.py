#!/usr/bin/env python3
"""
PoC 7 — BagIt SIP construction for the DOL preservation workflow.

Consumes one completed PoC 6 object from:
    <preparation>/<JOB_ID>/

Creates one staged BagIt SIP at:
    <staging>/<JOB_ID>/

Design:
- Verifies PoC 6 is complete.
- Builds a CLEAN COPY for staging, so the working preparation area is not
  destructively rearranged during the proof of concept.
- Excludes transient *_src.pdf files from the staged payload.
- Excludes obsolete DOME source-template placeholders.
- Excludes PoC working/control artefacts that are not part of the preservation
  package, while retaining substantive preservation evidence.
- Creates BagIt 0.97 tags and SHA-512 manifests.
- Performs an independent validation after bag creation.
- Writes a PoC 7 record OUTSIDE the bag, so the bag does not invalidate itself.

No external Python package is required; this PoC implements the RFC 8493
BagIt layout directly and validates all manifests itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from workflow_common import load, coverage, verify, write_record, safe_relative, working_pdf
from datetime import date, datetime, timezone
from pathlib import Path


BAGIT_VERSION = "0.97"
TAG_FILE_ENCODING = "UTF-8"
SOURCE_ORG_DEFAULT = (
    "World Trade Organization; Languages, Documentation and Information "
    "Management Division; Official Documents and Registry of Notifications Section"
)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )


def manifest_path(path: Path, bag_root: Path) -> str:
    # BagIt manifests use forward slashes regardless of host OS.
    return path.relative_to(bag_root).as_posix()


def should_exclude(relative_path: Path):
    """
    Return (exclude: bool, reason: str).

    The formal workflow retains:
      - rep_1 sources
      - rep_2 preservation masters
      - metadata
      - veraPDF / QA / DROID evidence
      - conversion evidence

    PoC control/read-model files are intentionally not payload preservation
    content. METS/PREMIS from PoC 6 are retained as metadata because this PoC
    explicitly generated them as preservation projections.
    """
    if "record-history" in relative_path.parts:
        return True, "historical working record revision"
    name = relative_path.name.lower()
    parts = [x.lower() for x in relative_path.parts]

    if working_pdf(relative_path):
        return True, "transient working PDF (_src) purged before packaging"

    if relative_path.parts[0] != "documentation":
        return False, ""

    if name.endswith("_dome_source_template.json"):
        return True, "obsolete DOME placeholder after final DOME validation"

    # Local PoC control records are implementation scaffolding, not archive content.
    if name.endswith("_processing_record.json"):
        return True, "PoC processing/control record"
    if "_poc1_" in name or "_poc2_" in name or "_poc3_" in name or "_poc4_" in name or "_poc5_" in name or "_poc6_" in name:
        return True, "PoC processing/control record"

    # DROID profile is a working application-state database, not the native
    # characterization report specified by the workflow.
    if name.endswith("_droid_profile.droid"):
        return True, "DROID working profile, not retained evidence"

    # Driver/debug logs are PoC diagnostics.
    if name.endswith("_driver.txt"):
        return True, "PoC driver/debug log"

    # Normalized helper JSON created by PoC 5 is convenient during development
    # but the raw DROID CSV is the retained tool evidence.
    if name.endswith("_droid_characterization.json"):
        return True, "PoC normalized DROID helper projection"

    return False, ""


def collect_payload_source_files(object_dir: Path):
    candidates = []
    exclusions = []

    for top in ("representations", "metadata", "documentation"):
        root = object_dir / top
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rp = p.relative_to(object_dir)
            exclude, reason = should_exclude(rp)
            if exclude:
                exclusions.append({
                    "relative_path": rp.as_posix(),
                    "reason": reason,
                    "sha512_before_exclusion": sha512(p),
                    "size_bytes": p.stat().st_size,
                })
            else:
                candidates.append((p, rp))

    return candidates, exclusions


def require_step6(object_dir: Path, job_id: str):
    record = object_dir / "documentation" / f"{job_id}_poc6_metadata_record.json"
    if not record.exists():
        raise RuntimeError(f"PoC 6 record not found: {record}")

    r = load_json(record)
    if r.get("status") != "STEP_6_METADATA_PROJECTED":
        raise RuntimeError(
            "PoC 6 is not complete. Expected STEP_6_METADATA_PROJECTED, got "
            + str(r.get("status"))
        )

    required = [
        object_dir / "metadata" / f"{job_id}_dome.xml",
        object_dir / "metadata" / f"{job_id}_premis.xml",
        object_dir / "metadata" / f"{job_id}_mets.xml",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError("Required metadata missing:\n  " + "\n  ".join(missing))

    from poc6_metadata import require_prior
    recs, droid = require_prior(object_dir, job_id)
    snapshot = {f["file_id"]: f["sha512_staged"] for f in recs[1]["files"]}
    if r.get("source_snapshot") != snapshot:
        raise RuntimeError("PoC6 metadata predates current sources; rerun PoC6")
    for key in ("dome", "premis", "mets"):
        verify(object_dir / r[key]["path"], r[key]["sha512"])
    folder_uuid = r.get("folder_uuid")
    if not folder_uuid:
        # fallback to PoC1 record if necessary
        p1 = object_dir / "documentation" / f"{job_id}_poc1_processing_record.json"
        if p1.exists():
            folder_uuid = load_json(p1).get("folder_uuid")
    if not folder_uuid:
        raise RuntimeError("Cannot determine object folder_UUID.")

    return r, folder_uuid


def build_clean_payload(object_dir: Path, bag_root: Path):
    data = bag_root / "data"
    data.mkdir(parents=True, exist_ok=True)

    included, excluded = collect_payload_source_files(object_dir)
    copied = []

    for src, rp in included:
        dst = data / rp
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

        src_hash = sha512(src)
        dst_hash = sha512(dst)
        if src_hash != dst_hash:
            raise RuntimeError(
                f"Copy-fixity mismatch while staging {rp.as_posix()}"
            )

        copied.append({
            "relative_path": rp.as_posix(),
            "source_sha512": src_hash,
            "staged_sha512": dst_hash,
            "size_bytes": dst.stat().st_size,
            "copy_fixity_verified": True,
        })

    return copied, excluded


def payload_oxum(bag_root: Path):
    files = [p for p in (bag_root / "data").rglob("*") if p.is_file()]
    return sum(p.stat().st_size for p in files), len(files)


def write_payload_manifest(bag_root: Path):
    lines = []
    files = sorted(p for p in (bag_root / "data").rglob("*") if p.is_file())
    for p in files:
        lines.append(f"{sha512(p)}  {manifest_path(p, bag_root)}")
    out = bag_root / "manifest-sha512.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out


def write_bagit_txt(bag_root: Path):
    p = bag_root / "bagit.txt"
    p.write_text(
        f"BagIt-Version: {BAGIT_VERSION}\n"
        f"Tag-File-Character-Encoding: {TAG_FILE_ENCODING}\n",
        encoding="utf-8",
        newline="\n",
    )
    return p


def write_bag_info(
    bag_root: Path,
    job_id: str,
    folder_uuid: str,
    bag_group_id: str,
    contact_name: str,
    source_org: str,
):
    octets, count = payload_oxum(bag_root)
    p = bag_root / "bag-info.txt"
    p.write_text(
        f"External-Identifier: {job_id}\n"
        f"Internal-Sender-Identifier: {folder_uuid}\n"
        f"Bag-Group-Identifier: {bag_group_id}\n"
        f"Source-Organization: {source_org}\n"
        f"Contact-Name: {contact_name}\n"
        f"Bagging-Date: {date.today().isoformat()}\n"
        f"Payload-Oxum: {octets}.{count}\n"
        f"Bag-Software-Agent: DPR PoC 7 Python BagIt builder (RFC 8493 layout)\n",
        encoding="utf-8",
        newline="\n",
    )
    return p


def write_tagmanifest(bag_root: Path):
    tag_files = sorted(
        p for p in bag_root.iterdir()
        if p.is_file() and p.name != "tagmanifest-sha512.txt"
    )
    lines = [f"{sha512(p)}  {p.name}" for p in tag_files]
    out = bag_root / "tagmanifest-sha512.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out


def parse_manifest_line(line: str):
    line = line.rstrip("\r\n")
    if not line:
        return None
    try:
        digest, path = line.split(None, 1)
    except ValueError:
        raise RuntimeError(f"Malformed manifest line: {line!r}")
    return digest, path.strip()


def validate_manifest(manifest: Path, bag_root: Path):
    problems = []
    seen = set()

    for raw in manifest.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_manifest_line(raw)
        if not parsed:
            continue
        expected, relp = parsed
        try:
            path = safe_relative(bag_root, relp)
        except (RuntimeError, ValueError) as exc:
            problems.append(str(exc))
            continue
        if relp in seen:
            problems.append(f"duplicate manifest path: {relp}")
        seen.add(relp.replace("\\", "/"))

        if not path.is_file():
            problems.append(f"missing: {relp}")
            continue

        actual = sha512(path)
        if actual.lower() != expected.lower():
            problems.append(
                f"checksum mismatch: {relp}: expected {expected}, got {actual}"
            )

    return problems, seen


def validate_bag(bag_root: Path):
    problems = []

    for required in (
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha512.txt",
        "tagmanifest-sha512.txt",
        "data",
    ):
        if not (bag_root / required).exists():
            problems.append(f"missing required bag component: {required}")

    if problems:
        return problems

    manifest_problems, listed_payload = validate_manifest(
        bag_root / "manifest-sha512.txt", bag_root
    )
    problems.extend(manifest_problems)

    actual_payload = {
        manifest_path(p, bag_root)
        for p in (bag_root / "data").rglob("*")
        if p.is_file()
    }

    missing_from_manifest = sorted(actual_payload - listed_payload)
    extra_in_manifest = sorted(listed_payload - actual_payload)

    for p in missing_from_manifest:
        problems.append(f"payload file absent from manifest: {p}")
    for p in extra_in_manifest:
        problems.append(f"manifest references non-payload file: {p}")

    tag_problems, listed_tags = validate_manifest(
        bag_root / "tagmanifest-sha512.txt", bag_root
    )
    problems.extend(tag_problems)

    expected_tags = {
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha512.txt",
    }
    for tag in expected_tags:
        if tag not in listed_tags:
            problems.append(f"tag file absent from tagmanifest: {tag}")

    return problems


def directory_snapshot(bag_root: Path):
    out = []
    for p in sorted(bag_root.rglob("*")):
        if p.is_file():
            out.append({
                "path": p.relative_to(bag_root).as_posix(),
                "size_bytes": p.stat().st_size,
                "sha512": sha512(p),
            })
    return out


def main():
    ap = argparse.ArgumentParser(description="PoC 7: create a SHA-512 BagIt SIP")
    ap.add_argument("--preparation-dir", required=True)
    ap.add_argument("--staging-dir", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument(
        "--contact-name",
        default="PoC Preservation Operator",
        help="Step-7 authorizer recorded in bag-info.txt",
    )
    ap.add_argument(
        "--bag-group-id",
        help="Batch correlation UUID. If omitted, a PoC UUID is minted.",
    )
    ap.add_argument(
        "--source-organization",
        default=SOURCE_ORG_DEFAULT,
    )
    ap.add_argument(
        "--replace-existing",
        action="store_true",
        help="Deprecated; destructive replacement is disabled. Use a new staging root.",
    )
    args = ap.parse_args()

    prep = Path(args.preparation_dir).resolve()
    staging = Path(args.staging_dir).resolve()
    obj = prep / args.job_id
    bag_root = staging / args.job_id

    try:
        if not obj.is_dir():
            raise RuntimeError(f"Preparation object not found: {obj}")

        p6, folder_uuid = require_step6(obj, args.job_id)

        if bag_root.exists():
            candidates, _ = collect_payload_source_files(obj)
            expected = {rp.as_posix(): sha512(src) for src,rp in candidates}
            actual = {p.relative_to(bag_root/"data").as_posix(): sha512(p) for p in (bag_root/"data").rglob("*") if p.is_file()}
            if expected == actual and not validate_bag(bag_root):
                print("[REUSE] Identical valid staging package")
                return 0
            if not args.replace_existing:
                raise RuntimeError(
                    f"Staging bag already exists: {bag_root}\n"
                    "Use --replace-existing only if you intentionally want to rebuild it."
                )
            raise RuntimeError("Destructive replacement is disabled; use a distinct --staging-dir for each incremental package")

        staging.mkdir(parents=True, exist_ok=True)
        bag_root.mkdir(parents=True)

        print("[INFO] PoC 6 gate: PASS")
        print(f"       JOB_ID:      {args.job_id}")
        print(f"       folder_UUID: {folder_uuid}")
        print(f"[INFO] Building clean staged payload at {bag_root}")

        copied, excluded = build_clean_payload(obj, bag_root)

        print(f"[INFO] Copied {len(copied)} retained payload file(s)")
        print(f"[INFO] Excluded {len(excluded)} transient/PoC working file(s)")
        for e in excluded:
            print(f"       EXCLUDE {e['relative_path']} | {e['reason']}")

        bag_group_id = args.bag_group_id or str(uuid.uuid4())

        write_bagit_txt(bag_root)
        write_payload_manifest(bag_root)
        write_bag_info(
            bag_root,
            args.job_id,
            folder_uuid,
            bag_group_id,
            args.contact_name,
            args.source_organization,
        )
        write_tagmanifest(bag_root)

        problems = validate_bag(bag_root)
        if problems:
            # Never leave an invalid bag in staging.
            failed = staging / f"{args.job_id}_FAILED_POC7"
            if failed.exists():
                failed = staging / f"{args.job_id}_FAILED_POC7_{uuid.uuid4().hex}"
            bag_root.rename(failed)
            print("[FATAL] Bag validation FAILED. Bag moved aside:", failed, file=sys.stderr)
            for p in problems:
                print("       " + p, file=sys.stderr)
            return 7

        octets, count = payload_oxum(bag_root)

        # PoC record must be outside bag: adding it inside afterward would change
        # the tagmanifest/payload and invalidate the package.
        record = {
            "poc": 7,
            "job_id": args.job_id,
            "folder_uuid": folder_uuid,
            "created_utc": utc_now(),
            "status": "STEP_7_BAGIT_SIP_STAGED_VALID",
            "staging_bag": str(bag_root),
            "bag_group_identifier": bag_group_id,
            "bagit_version": BAGIT_VERSION,
            "checksum_algorithm": "SHA-512",
            "payload_oxum": f"{octets}.{count}",
            "payload_file_count": count,
            "payload_total_octets": octets,
            "copy_fixity_verified_file_count": sum(
                1 for x in copied if x["copy_fixity_verified"]
            ),
            "excluded_before_packaging": excluded,
            "bag_validation": {
                "outcome": "PASS",
                "manifest": "manifest-sha512.txt",
                "tagmanifest": "tagmanifest-sha512.txt",
            },
            "bag_snapshot": directory_snapshot(bag_root),
            "event_projection": [
                {
                    "event": "WorkingFilePurged",
                    "eventType": "deletion",
                    "objects": [
                        x["relative_path"]
                        for x in excluded
                        if x["relative_path"].lower().endswith("_src.pdf")
                    ],
                    "note": "PoC implementation purges _src from the staged SIP copy; preparation workspace remains unchanged.",
                },
                {
                    "event": "PreparationPackageAssembled",
                    "eventType": "packing",
                    "outcome": "success",
                },
                {
                    "event": "SubmissionPackageCreated",
                    "eventType": "information package creation",
                    "outcome": "success",
                },
                {
                    "event": "PackageTransmitted",
                    "eventType": "transfer",
                    "outcome": "staged",
                },
            ],
            "next_gate": "PoC 8 — independent ingest-worker BagIt verification",
        }

        record_path = staging / f"{args.job_id}_poc7_bagit_record.json"
        write_record(record_path, record)

        print()
        print(f"[PASS] BagIt SIP created and independently validated")
        print(f"       bag:          {bag_root}")
        print(f"       payload:      {count} file(s), {octets} octets")
        print(f"       Payload-Oxum: {octets}.{count}")
        print(f"       SHA-512 manifest:     {bag_root / 'manifest-sha512.txt'}")
        print(f"       SHA-512 tagmanifest:  {bag_root / 'tagmanifest-sha512.txt'}")
        print(f"       PoC 7 record:         {record_path}")
        print("Status: STEP_7_BAGIT_SIP_STAGED_VALID")
        return 0

    except Exception as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
