#!/usr/bin/env python3
"""DOL Digital Preservation PoC 1: DOL pairs and PDF-only notification attachments."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import uuid
from workflow_common import write_record, digest, safe_relative
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
LANG_MAP = {
    "e": "en", "en": "en", "eng": "en", "english": "en",
    "f": "fr", "fr": "fr", "fra": "fr", "fre": "fr", "french": "fr",
    "s": "es", "es": "es", "spa": "es", "spanish": "es",
}

class PocError(Exception):
    pass

@dataclass
class FileRecord:
    file_id: str
    role: str
    language: str
    original_name: str
    original_path: str
    staged_name: str
    staged_path: str
    sha512_source: str
    sha512_staged: str
    fixity_verified: bool
    size_bytes: int

@dataclass
class ObjectRecord:
    job_id: str
    folder_uuid: str
    created_at: str
    preparation_folder: str
    status: str
    files: list[FileRecord]

def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def sha512_file(path):
    digest = hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()

def normalise_language(raw):
    key = (raw or "").strip().lower()
    if key not in LANG_MAP:
        raise PocError(f"Unsupported language value: {raw!r}")
    return LANG_MAP[key]

def validate_job_id(job_id):
    job_id = (job_id or "").strip()
    if len(job_id) != 11 or job_id[4] != "_" or not job_id[:4].isdigit() or not job_id[5:].isdigit():
        raise PocError(f"Invalid JOB_ID {job_id!r}; expected YYYY_NNNNNN")
    return job_id

def create_structure(base, job_id):
    obj = base / job_id
    for rel in (
        "representations/rep_1/data/en",
        "representations/rep_1/data/fr",
        "representations/rep_1/data/es",
        "representations/rep_2/data/en",
        "representations/rep_2/data/fr",
        "representations/rep_2/data/es",
        "representations/rep_2/data/attachments",
        "metadata",
        "documentation",
    ):
        (obj / rel).mkdir(parents=True, exist_ok=True)
    return obj

def require_source(input_dir, filename, field_name):
    filename = (filename or "").strip()
    if not filename:
        raise PocError(f"Missing {field_name}")
    source = safe_relative(input_dir, filename)
    if not source.is_file():
        raise PocError(f"Source file does not exist: {source}")
    return source

def copy_verified(source, destination):
    before = sha512_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        after = sha512_file(destination)
        if before != after:
            raise PocError(f"Canonical staged file already exists with different content: {destination}")
        return before, after, True
    shutil.copy2(source, destination)
    after = sha512_file(destination)
    return before, after, before == after

def stage_file(*, source, destination, role, language, original_path=None):
    src_hash, staged_hash, verified = copy_verified(source, destination)
    if not verified:
        destination.unlink(missing_ok=True)
        raise PocError(f"Fixity mismatch after copy: {source} -> {destination}")
    return FileRecord(
        file_id=str(uuid.uuid4()), role=role, language=language,
        original_name=Path(original_path).name if original_path else source.name, original_path=original_path or str(source.resolve()),
        staged_name=destination.name, staged_path=str(destination.resolve()),
        sha512_source=src_hash, sha512_staged=staged_hash,
        fixity_verified=True, size_bytes=source.stat().st_size,
    )

def load_existing_object_record(obj_dir, job_id):
    path = obj_dir / "documentation" / f"{job_id}_poc1_processing_record.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ObjectRecord(
            job_id=str(data.get("job_id") or job_id),
            folder_uuid=str(data.get("folder_uuid") or data.get("uuid") or ""),
            created_at=str(data.get("created_at") or utc_now()),
            preparation_folder=str(data.get("preparation_folder") or obj_dir.resolve()),
            status=str(data.get("status") or ""),
            files=[FileRecord(**f) for f in data.get("files", [])],
        )
    except Exception as exc:
        raise PocError(f"Cannot read existing PoC1 record {path}: {exc}") from exc

def merge_file_record(existing, new_file):
    # Never merge by role/language: two English attachments are distinct files.
    for idx, old in enumerate(existing):
        if old.staged_path == new_file.staged_path:
            new_file.file_id = old.file_id
            if old.sha512_staged != new_file.sha512_staged:
                raise PocError(f"Existing record fixity differs: {old.staged_path}")
            return old
    existing.append(new_file)
    return new_file

def is_notification_attachment(row):
    return (
        row.get("document_kind") == "notification" or (
        not (row.get("source_file") or "").strip()
        and "_notification_" in (row.get("pdf_file") or "").lower())
    )

def process_row(row, input_dir, preparation_dir):
    job_id = validate_job_id(row.get("job_id", ""))
    attachment = is_notification_attachment(row)
    if attachment:
        raw_lang = (row.get("language") or "").strip().lower()
        lang = LANG_MAP.get(raw_lang, "und")
    else:
        lang = normalise_language(row.get("language", ""))

    pdf_source = require_source(input_dir, row.get("pdf_file", ""), "pdf_file")
    if pdf_source.suffix.lower() != ".pdf":
        raise PocError(f"pdf_file must be a PDF: {pdf_source.name}")

    # Validate DOL authoring source before staging any files.
    authoring_source = None
    if not attachment:
        authoring_source = require_source(input_dir, row.get("source_file", ""), "source_file")
        if authoring_source.suffix.lower() not in {".doc", ".docx"}:
            raise PocError(f"Authoring source has no extension: {authoring_source.name}")

    obj_dir = create_structure(preparation_dir, job_id)
    old = load_existing_object_record(obj_dir, job_id)
    folder_uuid = old.folder_uuid if old and old.folder_uuid else str(uuid.uuid4())
    created_at = old.created_at if old else utc_now()
    files = list(old.files) if old else []

    if attachment:
        dest = obj_dir / "representations" / "rep_2" / "data" / "attachments" / pdf_source.name
        origin = row.get("pdf_original_path") or str(pdf_source.resolve())
        previous = next((f for f in files if f.role == "notification_attachment" and f.original_path == origin and f.sha512_staged == digest(pdf_source)), None)
        if previous:
            dest = Path(previous.staged_path)
        elif dest.exists():
            token = hashlib.sha256((origin + digest(pdf_source)).encode()).hexdigest()[:16]
            dest = dest.with_name(dest.stem + "_" + token + dest.suffix)
        merge_file_record(files, stage_file(
            source=pdf_source, destination=dest,
            role="notification_attachment", language=lang, original_path=row.get("pdf_original_path")
        ))
        print(f"[NOTIFICATION] {pdf_source.name} -> {dest}")
    else:
        pair_token = hashlib.sha256((str(authoring_source.resolve()).casefold() + digest(authoring_source) + digest(pdf_source)).encode()).hexdigest()[:16]
        prior = next((f for f in files if f.role == "published_pdf_working_source" and (f.original_path == (row.get("pdf_original_path") or str(pdf_source.resolve())) or f.original_name == pdf_source.name) and f.sha512_staged == digest(pdf_source)), None)
        if prior and not any(f.role == "authoring_source" and (f.original_path == (row.get("source_original_path") or str(authoring_source.resolve())) or f.original_name == authoring_source.name) and f.sha512_staged == digest(authoring_source) for f in files):
            prior = None
        occupied = any(f.role == "published_pdf_working_source" and f.language == lang for f in files)
        suffix = "_" + pair_token if occupied else ""
        if prior:
            suffix = Path(prior.staged_path).stem[len(f"{job_id}_{lang}"):-4]
        rep1_dest = obj_dir / "representations" / "rep_1" / "data" / lang / f"{job_id}_{lang}{suffix}{authoring_source.suffix.lower()}"
        rep2_dest = obj_dir / "representations" / "rep_2" / "data" / lang / f"{job_id}_{lang}{suffix}_src.pdf"
        merge_file_record(files, stage_file(
            source=authoring_source, destination=rep1_dest,
            role="authoring_source", language=lang, original_path=row.get("source_original_path")
        ))
        merge_file_record(files, stage_file(
            source=pdf_source, destination=rep2_dest,
            role="published_pdf_working_source", language=lang, original_path=row.get("pdf_original_path")
        ))

    record = ObjectRecord(
        job_id=job_id, folder_uuid=folder_uuid, created_at=created_at,
        preparation_folder=str(obj_dir.resolve()),
        status="STEP_1_PREPARED_FIXITY_VERIFIED", files=files,
    )
    record_path = obj_dir / "documentation" / f"{job_id}_poc1_processing_record.json"
    write_record(record_path, asdict(record))
    return record

def failure_record_path(preparation_dir, job_id, line_no):
    return preparation_dir / "_poc1_failures" / f"{job_id}_line_{line_no}_poc1_failure_record.json"

def write_failure_record(*, row, line_no, preparation_dir, error):
    try:
        job_id = validate_job_id(row.get("job_id", ""))
    except PocError:
        return None
    path = failure_record_path(preparation_dir, job_id, line_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job_id, "created_at": utc_now(),
        "status": "STEP_1_PREPARATION_FAILED", "error": str(error),
        "csv_line": line_no,
        "input": {k: row.get(k, "") for k in ("language", "pdf_file", "source_file")},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

def clear_failure_record_for_line(preparation_dir, job_id, line_no):
    failure_record_path(preparation_dir, job_id, line_no).unlink(missing_ok=True)

def read_csv(csv_path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"job_id", "language", "pdf_file", "source_file"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise PocError(f"CSV missing required columns: {', '.join(sorted(missing))}")
        yield from reader

def main():
    parser = argparse.ArgumentParser(description="DOL DPR PoC1 builder")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--preparation-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if not args.csv.is_file():
            raise PocError(f"CSV does not exist: {args.csv}")
        if not args.input_dir.is_dir():
            raise PocError(f"Input directory does not exist: {args.input_dir}")
        args.preparation_dir.mkdir(parents=True, exist_ok=True)
        rows = list(read_csv(args.csv))
        if not rows:
            raise PocError("CSV contains no data rows")
        successes = failures = 0
        successful_job_ids = set()
        for line_no, row in enumerate(rows, start=2):
            try:
                record = process_row(row, args.input_dir, args.preparation_dir)
                clear_failure_record_for_line(args.preparation_dir, record.job_id, line_no)
                successes += 1
                successful_job_ids.add(record.job_id)
                print(f"[OK] {record.job_id} [{row.get('language', '')}] -> {record.preparation_folder}")
            except Exception as exc:
                failures += 1
                failure_path = write_failure_record(
                    row=row, line_no=line_no, preparation_dir=args.preparation_dir, error=exc
                )
                print(f"[ERROR] CSV line {line_no}: {exc}", file=sys.stderr)
                if failure_path:
                    print(f"        Failure record: {failure_path}", file=sys.stderr)
        print(f"\nCompleted: {successes} rows succeeded; {failures} failed; {len(successful_job_ids)} JOB_IDs prepared")
        return 0 if failures == 0 else 2
    except PocError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
