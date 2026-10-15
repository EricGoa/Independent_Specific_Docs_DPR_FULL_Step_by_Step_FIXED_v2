#!/usr/bin/env python3
"""
PoC 5 â€” DROID source-format characterization using direct Java + DROID JAR invocation.

LOGICAL PLACEMENT:
  Run immediately after PoC 1 and BEFORE PoC 2 conversion.

Purpose:
  Answer "What did we receive?" by identifying the staged source files
  before any preservation-format conversion occurs.

Designed for managed Windows environments where droid.bat may be blocked by
group policy. Uses:

    <java.exe> -jar <droid-command-line.jar> ...

Prerequisites:
  - PoC 1 PASS
  - DROID 6.8.1 (or compatible) package with bundled Java 21

Compatibility note:
  The script/file remains named PoC 5 and retains STEP_5_DROID_CHARACTERIZED
  so downstream PoC 6 compatibility is preserved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import uuid
from workflow_common import source_paths, load, write_record, verify, registered_uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def norm_key(s: str) -> str:
    return "".join(ch.lower() for ch in s.strip() if ch.isalnum())


def find_col(fieldnames, *candidates):
    mapping = {norm_key(x): x for x in (fieldnames or [])}
    for c in candidates:
        if norm_key(c) in mapping:
            return mapping[norm_key(c)]
    return None


def discover_source_files(object_dir: Path):
    """
    Characterize only the files received/staged by PoC 1:

      rep_1/.../<JOB_ID>_<lang>.<authoring-extension>
      rep_2/.../<JOB_ID>_<lang>_src.pdf
      rep_2/data/attachments/* (all staged notification attachments)

    The preservation PDF/A master (<JOB_ID>_<lang>.pdf) is intentionally
    excluded because DROID now runs before PoC 2 conversion.
    """
    return source_paths(object_dir)


def run(cmd, cwd=None):
    return subprocess.run(
        [str(x) for x in cmd],
        cwd=cwd,
        text=True,
        capture_output=True,
    )


def verify_runtime(java: Path, jar: Path):
    if not java.exists():
        raise FileNotFoundError(f"Java runtime not found: {java}")
    if not jar.exists():
        raise FileNotFoundError(f"DROID command-line JAR not found: {jar}")

    r = run([java, "-version"])
    # java -version normally writes to stderr
    text = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode != 0:
        raise RuntimeError(f"Java failed to start:\n{text}")

    h = run([java, "-jar", jar, "-h"], cwd=jar.parent)
    if h.returncode != 0:
        raise RuntimeError(
            "DROID CLI failed its help/startup test.\n"
            f"STDOUT:\n{h.stdout}\nSTDERR:\n{h.stderr}"
        )
    return text


def droid_characterize(java: Path, jar: Path, files, docs: Path, job_id: str):
    """
    Use DROID's profile workflow:
      java -jar droid-command-line.jar -a <files...> -p <profile>
      java -jar droid-command-line.jar -p <profile> -e <csv>
    """
    profile = docs / f"{job_id}_droid_profile.droid"
    csv_out = docs / f"{job_id}_droid_export.csv"
    driver = docs / f"{job_id}_droid_driver.txt"

    if any(p.exists() for p in (profile, csv_out, driver)):
        token = uuid.uuid4().hex
        profile = docs / f"{job_id}_{token}_droid_profile.droid"
        csv_out = docs / f"{job_id}_{token}_droid_export.csv"
        driver = docs / f"{job_id}_{token}_droid_driver.txt"

    base = [java, "-jar", jar]

    add_cmd = [
        *base,
        "-a",
        *[str(x) for x in files],
        "-p",
        str(profile),
    ]
    r1 = run(add_cmd, cwd=jar.parent)

    export_cmd = [
        *base,
        "-p",
        str(profile),
        "-e",
        str(csv_out),
    ]
    r2 = run(export_cmd, cwd=jar.parent)

    driver.write_text(
        "PoC 5 DROID direct-Java driver log\n"
        f"UTC: {utc_now()}\n\n"
        "PROFILE COMMAND:\n" + " ".join(map(str, add_cmd)) +
        f"\n\nEXIT: {r1.returncode}\nSTDOUT:\n{r1.stdout}\nSTDERR:\n{r1.stderr}\n\n"
        "EXPORT COMMAND:\n" + " ".join(map(str, export_cmd)) +
        f"\n\nEXIT: {r2.returncode}\nSTDOUT:\n{r2.stdout}\nSTDERR:\n{r2.stderr}\n",
        encoding="utf-8",
    )

    if r1.returncode != 0:
        raise RuntimeError(
            f"DROID profiling failed (exit {r1.returncode}). See:\n{driver}"
        )
    if r2.returncode != 0 or not csv_out.exists():
        raise RuntimeError(
            f"DROID CSV export failed (exit {r2.returncode}). See:\n{driver}"
        )

    return profile, csv_out, driver


def parse_droid(csv_path: Path, target_files, object_dir: Path):
    targets_by_abs = {str(p.resolve()).lower(): p for p in target_files}
    targets_by_name = {}
    for p in target_files:
        targets_by_name.setdefault(p.name.lower(), []).append(p)

    results = {str(p.resolve()).lower(): [] for p in target_files}

    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []

        name_col = find_col(fields, "NAME", "FILE_NAME", "FILENAME")
        path_col = find_col(fields, "FILE_PATH", "PATH", "URI")
        type_col = find_col(fields, "TYPE")
        method_col = find_col(fields, "METHOD")
        status_col = find_col(fields, "STATUS")
        puid_col = find_col(fields, "PUID")
        mime_col = find_col(fields, "MIME_TYPE", "MIMETYPE")
        fmt_col = find_col(fields, "FORMAT_NAME", "FORMAT")
        ver_col = find_col(fields, "FORMAT_VERSION", "VERSION")
        size_col = find_col(fields, "SIZE")

        for row in reader:
            matched = None

            if path_col and row.get(path_col):
                raw = row[path_col].strip().strip('"')
                candidate = raw
                if candidate.lower().startswith("file:/"):
                    candidate = candidate[6:]
                candidate = candidate.replace("/", os.sep)
                try:
                    matched = targets_by_abs.get(
                        str(Path(candidate).resolve()).lower()
                    )
                except Exception:
                    pass

            if matched is None and name_col and row.get(name_col):
                candidates = targets_by_name.get(
                    row[name_col].strip().lower(), []
                )
                if len(candidates) == 1:
                    matched = candidates[0]

            if matched is None:
                continue

            results[str(matched.resolve()).lower()].append({
                "puid": row.get(puid_col, "") if puid_col else "",
                "mime_type": row.get(mime_col, "") if mime_col else "",
                "format_name": row.get(fmt_col, "") if fmt_col else "",
                "format_version": row.get(ver_col, "") if ver_col else "",
                "identification_method": row.get(method_col, "") if method_col else "",
                "droid_status": row.get(status_col, "") if status_col else "",
                "droid_type": row.get(type_col, "") if type_col else "",
                "droid_size": row.get(size_col, "") if size_col else "",
            })

    file_ids = {str(Path(f["staged_path"]).resolve()).lower(): f["file_id"] for f in load(object_dir / "documentation" / f"{object_dir.name}_poc1_processing_record.json").get("files", [])}
    parsed = []
    for p in target_files:
        ids = results.get(str(p.resolve()).lower(), [])
        parsed.append({
            "file_id": file_ids.get(str(p.resolve()).lower()),
            "relative_path": p.relative_to(object_dir).as_posix(),
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "sha512": sha512(p),
            "identifications": ids,
            "identified": bool(
                ids and any(x.get("puid") or x.get("format_name") for x in ids)
            ),
        })

    return parsed


def main():
    ap = argparse.ArgumentParser(
        description="PoC 5: DROID characterization via direct Java/JAR"
    )
    ap.add_argument("--preparation-dir", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--java", required=True, help="Full path to bundled Java 21 java.exe")
    ap.add_argument("--droid-jar", required=True, help="Full path to droid-command-line-*.jar")
    args = ap.parse_args()

    prep = Path(args.preparation_dir).resolve()
    obj = prep / args.job_id
    docs = obj / "documentation"
    java = Path(args.java).resolve()
    jar = Path(args.droid_jar).resolve()

    if not obj.exists():
        print(f"[FATAL] Object not found: {obj}", file=sys.stderr)
        return 2

    p1 = docs / f"{args.job_id}_poc1_processing_record.json"

    if not p1.exists():
        print(
            "[FATAL] PoC 1 record is required before DROID source characterization.",
            file=sys.stderr,
        )
        return 2

    p1data = load_json(p1)

    if str(p1data.get("status", "")).upper() != "STEP_1_PREPARED_FIXITY_VERIFIED":
        print(
            f"[FATAL] PoC 1 did not pass. Status: {p1data.get('status')}",
            file=sys.stderr,
        )
        return 3

    report_path = docs / f"{args.job_id}_droid_characterization.json"
    previous = load(report_path)
    wanted = {p.relative_to(obj).as_posix(): sha512(p) for p in source_paths(obj)}
    actual = {f["relative_path"]: f["sha512"] for f in previous.get("files", [])}
    prior_record = load(docs / f"{args.job_id}_poc5_characterization_record.json")
    if wanted == actual and prior_record.get("status") == "STEP_5_DROID_CHARACTERIZED":
        for evidence in prior_record.get("evidence_files", []):
            verify(obj / evidence["relative_path"], evidence["sha512"])
        print("[REUSE] DROID source coverage and evidence verified")
        return 0
    try:
        java_info = verify_runtime(java, jar)
    except Exception as e:
        print(f"[FATAL] Runtime check failed:\n{e}", file=sys.stderr)
        return 5

    files = [p for p in discover_source_files(obj) if actual.get(p.relative_to(obj).as_posix()) != wanted[p.relative_to(obj).as_posix()]]
    if not files:
        files = discover_source_files(obj)
    if not files:
        print("[FATAL] No PoC 1 source files found to characterize.", file=sys.stderr)
        return 6

    print("[INFO] DROID direct-Java runtime OK")
    print(f"       Java: {java}")
    print(f"       JAR:  {jar}")
    print(f"[INFO] Characterizing {len(files)} PoC 1 source file(s) before conversion...")

    for p in files:
        print(f"       {p.relative_to(obj)}")

    try:
        profile, csv_out, driver = droid_characterize(
            java, jar, files, docs, args.job_id
        )
        new_parsed = parse_droid(csv_out, files, obj)
        refreshed = {f["relative_path"] for f in new_parsed}
        parsed = sorted([f for f in previous.get("files", []) if f["relative_path"] in wanted and f["relative_path"] not in refreshed] + new_parsed, key=lambda f: f["relative_path"])
    except Exception as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 7

    report_path = docs / f"{args.job_id}_droid_characterization.json"

    report = {
        "poc": 5,
        "job_id": args.job_id,
        "created_utc": utc_now(),
        "tool": "DROID",
        "invocation": "direct_java_jar",
        "java_executable": str(java),
        "droid_jar": str(jar),
        "characterization_scope": "SOURCE_FILES",
        "characterization_timing": "PRE_CONVERSION",
        "source_definition": "PoC 1 staged authoring sources, published *_src.pdf, and rep_2/data/attachments files",
        "files": parsed,
        "summary": {
            "file_count": len(parsed),
            "identified_count": sum(1 for x in parsed if x["identified"]),
            "unidentified_count": sum(1 for x in parsed if not x["identified"]),
        },
    }
    immutable_report = docs / f"{args.job_id}_{uuid.uuid4().hex}_droid_characterization.json"
    write_record(immutable_report, report)
    write_record(report_path, report)

    evidence = [e for e in prior_record.get("evidence_files", []) if e.get("role") != "droid_characterization_report"]
    for e in evidence:
        verify(obj / e["relative_path"], e["sha512"])
    for role, p in [
        ("droid_csv_export", csv_out),
        ("droid_characterization_report", immutable_report),
        ("droid_driver_log", driver),
    ]:
        evidence.append({
            "file_id": registered_uuid(obj,"file",str(p.resolve())+sha512(p)),
            "role": role,
            "relative_path": p.relative_to(obj).as_posix(),
            "size_bytes": p.stat().st_size,
            "sha512": sha512(p),
        })

    record_path = docs / f"{args.job_id}_poc5_characterization_record.json"
    record = {
        "poc": 5,
        "job_id": args.job_id,
        "created_utc": utc_now(),
        "status": "STEP_5_DROID_CHARACTERIZED",
        "tool": "DROID",
        "invocation": "direct_java_jar",
        "characterization_scope": "SOURCE_FILES",
        "characterization_timing": "PRE_CONVERSION",
        "characterized_file_count": len(parsed),
        "identified_count": report["summary"]["identified_count"],
        "unidentified_count": report["summary"]["unidentified_count"],
        "evidence_files": evidence,
        "working_profile": {
            "relative_path": profile.relative_to(obj).as_posix(),
            "retention_note": "PoC working artefact; production retention policy TBD.",
        },
        "next_gate": "PoC 2 â€” PDF to PDF/A preservation conversion",
    }
    write_record(record_path, record)

    print()
    print(f"[OK] DROID characterized {len(parsed)} source file(s) before conversion")
    print(f"     identified:   {report['summary']['identified_count']}")
    print(f"     unidentified: {report['summary']['unidentified_count']}")
    print(f"     report: {report_path}")
    print(f"     raw CSV: {csv_out}")
    print(f"PoC 5 record: {record_path}")
    print("Scope: SOURCE_FILES / PRE_CONVERSION")
    print("Next: PoC 2 â€” PDF to PDF/A conversion")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())