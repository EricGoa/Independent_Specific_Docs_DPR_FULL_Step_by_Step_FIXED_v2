"""Shared fixity, record revision and coverage safeguards (no production actions on import)."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

SOURCE_ROLES = {"published_pdf_working_source", "notification_attachment"}

def digest(path):
    h = hashlib.sha512()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ({} if default is None else default)

def archive(path):
    path = Path(path)
    if path.is_file():
        history = path.parent / "record-history"
        history.mkdir(exist_ok=True)
        target = history / (path.stem + "_" + digest(path)[:24] + path.suffix)
        if not target.exists():
            shutil.copy2(path, target)

def write_record(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and load(path) == data:
        return
    archive(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def verify(path, expected):
    if not expected or digest(path).lower() != expected.lower():
        raise RuntimeError(f"Missing or mismatched SHA-512: {path}")

def source_records(obj):
    obj = Path(obj)
    return load(obj / "documentation" / f"{obj.name}_poc1_processing_record.json").get("files", [])

def received_pdfs(obj):
    return [f for f in source_records(obj) if f.get("role") in SOURCE_ROLES]

def source_paths(obj):
    files = source_records(obj)
    paths = []
    for f in files:
        p = Path(f["staged_path"])
        p.resolve().relative_to(Path(obj).resolve())
        verify(p, f["sha512_staged"])
        paths.append(p)
    return sorted(paths)

def master_for(source):
    source = Path(source)
    if source.name.lower().endswith("_src.pdf"):
        return source.with_name(source.name[:-8] + ".pdf")
    return source.with_name(source.stem + "_preservation.pdf")

def coverage(obj, recs, droid=None):
    """Refuse stale PASS records after incremental arrivals or changed bitstreams."""
    files = source_records(obj)
    source_paths(obj)
    expected = {f["file_id"] for f in files if f["role"] in SOURCE_ROLES}
    conversions = recs[2].get("conversions", [])
    ids = [c.get("source_file_id") for c in conversions]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise RuntimeError("Conversion coverage differs from current PoC1 sources; run PoC2")
    masters = {c["master_file_id"] for c in conversions}
    by_id = {f["file_id"]: f for f in files}
    by_master = {c["master_file_id"]: c for c in conversions}
    for c in conversions:
        original = by_id[c["source_file_id"]]
        if Path(c["source_path"]).resolve() != Path(original["staged_path"]).resolve() or c["source_sha512"] != original["sha512_staged"]:
            raise RuntimeError("Conversion source identity/fixity does not match PoC1")
        verify(c["source_path"], c["source_sha512"])
        verify(c["master_path"], c["master_sha512"])
    for stage, key in ((3, "validations"), (4, "qa_entries")):
        if stage not in recs:
            continue
        entries = recs[stage].get(key, [])
        entry_ids = [e.get("master_file_id") for e in entries]
        if len(entry_ids) != len(set(entry_ids)) or set(entry_ids) != masters:
            raise RuntimeError(f"PoC{stage} coverage is stale; rerun the stage")
        for e in entries:
            conv = by_master[e["master_file_id"]]
            if Path(e["master_path"]).resolve() != Path(conv["master_path"]).resolve():
                raise RuntimeError(f"PoC{stage} master identity does not match PoC2")
            if stage == 3:
                if e.get("verdict") != "PASS" or not e.get("conformance_validated") or e.get("master_sha512") != conv["master_sha512"]:
                    raise RuntimeError("Validation is not a passing verdict for the current master")
                verify(e["validation_report_path"], e["validation_report_sha512"])
            else:
                if e.get("status") != "AUTO_QA_CLEAR" or e.get("source_file_id") != conv["source_file_id"]:
                    raise RuntimeError("QA is not clear for the current source/master pair")
                verify(e["qa_report_path"], e["qa_report_sha512"])
                report = load(e["qa_report_path"])
                if report["source"]["sha512"] != conv["source_sha512"] or report["master"]["sha512"] != conv["master_sha512"]:
                    raise RuntimeError("QA report source/master fixity differs from conversion")
    if droid is not None:
        actual = {f["relative_path"]: f["sha512"] for f in droid.get("files", [])}
        wanted = {Path(f["staged_path"]).relative_to(obj).as_posix(): f["sha512_staged"] for f in files}
        if actual != wanted:
            raise RuntimeError("DROID source coverage/fixity is stale; rerun PoC5")

def input_copy(source, destination):
    """Keep separate receipts for distinct origins and changed content."""
    source, destination = Path(source), Path(destination)
    checksum = digest(source)
    receipt_path = destination.parent / ".receipt_manifest.json"
    receipts = load(receipt_path)
    origin = str(source.resolve())
    previous = receipts.get(destination.name)
    if (previous and previous["original_path"].casefold() != origin.casefold()) or (destination.exists() and digest(destination) != checksum):
        token = hashlib.sha256((origin.casefold() + checksum).encode()).hexdigest()[:16]
        destination = destination.with_name(destination.stem + "_" + token + destination.suffix)
    if destination.exists():
        verify(destination, checksum)
    else:
        shutil.copy2(source, destination)
        verify(destination, checksum)
    receipts[destination.name] = {"original_path": origin, "sha512": checksum}
    write_record(receipt_path, receipts)
    return destination


def safe_relative(root, relative):
    relative = str(relative).replace("\\", "/")
    if not relative or ":" in relative or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise RuntimeError(f"Unsafe package path: {relative}")
    target = Path(root) / relative
    target.resolve().relative_to(Path(root).resolve())
    return target


def working_pdf(relative):
    relative = Path(relative)
    return "attachments" not in relative.parts and relative.name.lower().endswith("_src.pdf")
