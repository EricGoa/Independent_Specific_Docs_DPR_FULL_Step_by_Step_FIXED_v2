#!/usr/bin/env python3
"""DOL Digital Preservation PoC 2 - Aspose orchestration.

Consumes a successful PoC 1 preparation object and, for each rep_2 *_src.pdf:
- invokes the modified Ed/Aspose .NET converter
- targets PDF/A-2b in strict ConvertErrorAction.None mode
- creates the master with its canonical final name (removes _src)
- keeps the _src working copy for later QA
- mints a new master file_id
- calculates SHA-512 for source and master
- records conversion provenance in a PoC JSON record

This PoC does NOT claim PDF/A conformance. PoC 3 / veraPDF is authoritative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from workflow_common import load, write_record, verify, received_pdfs, source_paths, master_for
from datetime import datetime, timezone
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


class Poc2Error(Exception):
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
        raise Poc2Error(f"Invalid JOB_ID {job_id!r}; expected YYYY_NNNNNN")
    return job_id


def load_poc1_record(obj_dir: Path, job_id: str) -> dict:
    path = obj_dir / "documentation" / f"{job_id}_poc1_processing_record.json"
    if not path.is_file():
        raise Poc2Error(f"PoC 1 processing record not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "STEP_1_PREPARED_FIXITY_VERIFIED":
        raise Poc2Error(f"PoC 1 object is not in the expected state: {data.get('status')!r}")
    return data


def discover_sources(obj_dir: Path) -> list[Path]:
    rep2_data = obj_dir / "representations" / "rep_2" / "data"
    if not rep2_data.is_dir():
        raise Poc2Error(f"rep_2 data folder not found: {rep2_data}")
    source_paths(obj_dir)
    sources = sorted(Path(f["staged_path"]) for f in received_pdfs(obj_dir))
    if not sources:
        raise Poc2Error(f"No *_src.pdf files found under {rep2_data}")
    return sources


def canonical_master(source: Path) -> Path:
    return master_for(source)


def run_converter(*, project: Path, license_path: Path, source: Path, master: Path, conversion_log: Path) -> tuple[str, str, str]:
    # Run the compiled managed DLL directly. This avoids Windows group-policy
    # blocks on the generated apphost .exe that dotnet run tries to launch.
    dll = project.parent / "bin" / "Debug" / "net8.0" / "DprPdfaConverter.dll"
    if not dll.is_file():
        raise Poc2Error(
            f"Compiled converter DLL not found: {dll}\n"
            f"Build it first with: dotnet build {project}"
        )

    cmd = [
        "dotnet", str(dll),
        "--input", str(source),
        "--output", str(master),
        "--log", str(conversion_log),
        "--license", str(license_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    status = "UNKNOWN"
    for line in (stdout + "\n" + stderr).splitlines():
        if line.startswith("STATUS="):
            status = line.split("=", 1)[1].strip()
            break

    if result.returncode != 0:
        raise Poc2Error(
            f"Aspose converter failed for {source.name} (exit {result.returncode}, status {status}).\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    if not master.is_file():
        raise Poc2Error(f"Converter returned success but master does not exist: {master}")

    return status, stdout, stderr


def main() -> int:
    parser = argparse.ArgumentParser(description="DOL Digital Preservation PoC 2 - Aspose PDF/A-2b conversion")
    parser.add_argument("--preparation-dir", required=True, type=Path, help="PoC 1 preparation root")
    parser.add_argument("--job-id", required=True, help="Canonical JOB_ID, e.g. 2025_003033")
    parser.add_argument("--converter-project", required=True, type=Path, help="Path to DprPdfaConverter.csproj")
    parser.add_argument("--license", required=True, type=Path, help="Path to the existing Aspose .lic file")
    args = parser.parse_args()

    try:
        job_id = validate_job_id(args.job_id)
        if not args.converter_project.is_file():
            raise Poc2Error(f"Converter project not found: {args.converter_project}")
        if not args.license.is_file():
            raise Poc2Error(f"Aspose licence not found: {args.license}")

        obj_dir = args.preparation_dir / job_id
        if not obj_dir.is_dir():
            raise Poc2Error(f"Prepared JOB_ID folder not found: {obj_dir}")

        poc1 = load_poc1_record(obj_dir, job_id)
        sources = discover_sources(obj_dir)
        documentation = obj_dir / "documentation"
        documentation.mkdir(parents=True, exist_ok=True)

        record_path = documentation / f"{job_id}_poc2_conversion_record.json"
        conversions = load(record_path).get("conversions", [])
        for source in sources:
            source_record = next(f for f in poc1["files"] if Path(f["staged_path"]).resolve() == source.resolve())
            previous = next((c for c in conversions if c.get("source_file_id") == source_record["file_id"]), None)
            if previous:
                verify(source, previous["source_sha512"])
                verify(previous["master_path"], previous["master_sha512"])
                for evidence_key in ("conversion_log", "driver_log"):
                    if previous.get(evidence_key + "_sha512"):
                        verify(previous[evidence_key], previous[evidence_key + "_sha512"])
                print(f"[REUSE] {source.name}")
                continue
            master = (source.parent / "preservation" / (source_record["file_id"] + ".pdf")) if source_record["role"] == "notification_attachment" else canonical_master(source)
            master.parent.mkdir(parents=True, exist_ok=True)
            if master.exists():
                raise Poc2Error(f"Refusing to overwrite existing master: {master}")

            base = master.stem
            conversion_log = documentation / f"{base}_conv_aspose.xml"
            driver_log = documentation / f"{base}_conv_driver.txt"
            if conversion_log.exists() or driver_log.exists():
                raise Poc2Error(f"Refusing to overwrite existing conversion evidence for {base}")

            source_sha512 = sha512_file(source)
            status, stdout, stderr = run_converter(
                project=args.converter_project,
                license_path=args.license,
                source=source,
                master=master,
                conversion_log=conversion_log,
            )
            master_sha512 = sha512_file(master)

            driver_log.write_text(
                "DPR PoC 2 Aspose invocation\n"
                f"timestamp_utc={utc_now()}\n"
                f"status={status}\n"
                f"source={source.resolve()}\n"
                f"master={master.resolve()}\n"
                f"target=PDF/A-2b\n"
                f"error_action=None\n\n"
                f"STDOUT\n{stdout}\n\nSTDERR\n{stderr}\n",
                encoding="utf-8",
            )

            conversions.append({
                "source_file_id": next(
                    (f.get("file_id") for f in poc1.get("files", []) if Path(f.get("staged_path", "")).resolve() == source.resolve()),
                    None,
                ),
                "source_role": source_record["role"],
                "language": source_record["language"],
                "source_path": str(source.resolve()),
                "source_sha512": source_sha512,
                "master_file_id": str(uuid.uuid4()),
                "master_path": str(master.resolve()),
                "master_sha512": master_sha512,
                "master_size_bytes": master.stat().st_size,
                "target_claimed": "PDF/A-2b",
                "conversion_mode": "strict",
                "convert_error_action": "None",
                "converter_status": status,
                "conversion_log": str(conversion_log.resolve()) if conversion_log.exists() else None,
                "driver_log": str(driver_log.resolve()),
                "driver_log_sha512": sha512_file(driver_log),
                "conversion_log_sha512": sha512_file(conversion_log) if conversion_log.exists() else None,
                "conformance_validated": False,
            })

            write_record(record_path, {"job_id": job_id, "folder_uuid": poc1.get("folder_uuid"), "status": "STEP_2_IN_PROGRESS", "conversions": conversions})
            print(f"[OK] {source.name} -> {master.name}")
            print(f"     Aspose status: {status}")
            print(f"     master file_id: {conversions[-1]['master_file_id']}")
            print(f"     SHA-512 master: {master_sha512[:32]}...")
            print("     veraPDF validated: NO")

        record = {
            "job_id": job_id,
            "folder_uuid": poc1.get("folder_uuid"),
            "created_at": utc_now(),
            "status": "STEP_2_MASTERS_CREATED_UNVALIDATED",
            "engine": {
                "vendor": "Aspose",
                "product": "Aspose.PDF for .NET",
                "package_version": "25.10.0",
                "target": "PDF/A-2b",
                "convert_error_action": "None",
            },
            "conversions": conversions,
            "note": "PoC processing record only; not the future pmsystem event store. PDF/A conformance remains unvalidated until veraPDF (PoC 3).",
        }
        record_path = documentation / f"{job_id}_poc2_conversion_record.json"
        write_record(record_path, record)

        print(f"\nCompleted: {len(conversions)} master(s) created, 0 failed")
        print(f"PoC 2 record: {record_path}")
        return 0

    except FileNotFoundError as exc:
        if exc.filename == "dotnet" or "dotnet" in str(exc):
            print("[FATAL] dotnet was not found. Install/use .NET 8 SDK or run from a machine where Ed's project already builds.", file=sys.stderr)
        else:
            print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    except Poc2Error as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[FATAL] Unexpected error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
