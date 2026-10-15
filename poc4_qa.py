#!/usr/bin/env python3
"""DOL Digital Preservation PoC 4 - fidelity QA.

Consumes a successful PoC 3 object and compares each validated PDF/A master
against its retained *_src.pdf source.

Automated checks implemented for every master:
  1. page count
  2. word-multiset text comparison
  3. descriptive metadata (Title, Author, Subject, Keywords)
  4. tagged-state / structure-tree presence
  5. navigational structures:
       - bookmarks/outlines, compared by resolved page destination + title
       - internal link annotations, compared by source page -> destination page
       - external URI annotations, compared as a multiset

The automated layer never auto-rejects a master. Any difference produces
FLAGGED_FOR_REVIEW. A clear result produces AUTO_QA_CLEAR. The master is never
modified.

Evidence:
  documentation/<base>_qa_pypdf.json
  documentation/<JOB_ID>_poc4_qa_record.json

This is a PoC processing record, not the future pmsystem event store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import uuid
from workflow_common import load, write_record, verify, coverage, registered_uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdfminer.high_level import extract_text
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

CHUNK_SIZE = 1024 * 1024
TOKEN_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
METADATA_FIELDS = {
    "Title": "/Title",
    "Author": "/Author",
    "Subject": "/Subject",
    "Keywords": "/Keywords",
}


class Poc4Error(Exception):
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
        raise Poc4Error(f"Invalid JOB_ID {job_id!r}; expected YYYY_NNNNNN")
    return job_id


def load_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise Poc4Error(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise Poc4Error(f"Could not read {label}: {path}: {exc}") from exc


def load_records(obj_dir: Path, job_id: str) -> tuple[dict, dict]:
    documentation = obj_dir / "documentation"
    poc2 = load_json(documentation / f"{job_id}_poc2_conversion_record.json", "PoC 2 record")
    poc3 = load_json(documentation / f"{job_id}_poc3_validation_record.json", "PoC 3 record")
    if poc2.get("status") != "STEP_2_MASTERS_CREATED_UNVALIDATED":
        raise Poc4Error(f"Unexpected PoC 2 status: {poc2.get('status')!r}")
    if poc3.get("status") != "STEP_3_VERAPDF_PASSED":
        raise Poc4Error(
            f"PoC 3 has not passed for this object: {poc3.get('status')!r}. "
            "PoC 4 must not run past a failed veraPDF gate."
        )
    return poc2, poc3


def deref(obj: Any) -> Any:
    try:
        return obj.get_object() if isinstance(obj, IndirectObject) else obj
    except Exception:
        return obj


def normalise_metadata_value(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value)).strip()


def metadata_snapshot(reader: PdfReader) -> dict[str, str]:
    md = reader.metadata or {}
    return {
        label: normalise_metadata_value(md.get(pdf_key))
        for label, pdf_key in METADATA_FIELDS.items()
    }


def is_tagged(reader: PdfReader) -> bool:
    root = deref(reader.trailer.get("/Root"))
    if not isinstance(root, DictionaryObject):
        return False
    struct = root.get("/StructTreeRoot")
    return struct is not None


def page_ref_map(reader: PdfReader) -> dict[tuple[int, int], int]:
    mapping: dict[tuple[int, int], int] = {}
    for idx, page in enumerate(reader.pages):
        ref = getattr(page, "indirect_reference", None)
        if isinstance(ref, IndirectObject):
            mapping[(ref.idnum, ref.generation)] = idx
    return mapping


def destination_page_number(reader: PdfReader, destination: Any) -> int | None:
    try:
        return reader.get_destination_page_number(destination)
    except Exception:
        return None


def flatten_outline(reader: PdfReader) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(items: Any, depth: int = 0) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = normalise_metadata_value(getattr(item, "title", ""))
            page_no = destination_page_number(reader, item)
            result.append({"title": title, "page": page_no, "depth": depth})

    try:
        walk(reader.outline)
    except Exception:
        return []
    return result


def resolve_dest_array_to_page(dest: Any, page_map: dict[tuple[int, int], int]) -> int | None:
    dest = deref(dest)
    if isinstance(dest, ArrayObject) and len(dest) > 0:
        first = dest[0]
        if isinstance(first, IndirectObject):
            return page_map.get((first.idnum, first.generation))
        first = deref(first)
        ref = getattr(first, "indirect_reference", None)
        if isinstance(ref, IndirectObject):
            return page_map.get((ref.idnum, ref.generation))
    return None


def extract_links(reader: PdfReader) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (internal_links, external_links).

    Internal links are represented by source page and resolved destination page.
    External links are represented by source page and URI.
    Rectangles are intentionally ignored in the PoC comparison because PDF/A
    normalisation may legitimately rewrite annotation geometry/object structure
    without changing navigation semantics.
    """
    page_map = page_ref_map(reader)
    internal: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []

    for page_idx, page in enumerate(reader.pages):
        annots = deref(page.get("/Annots", []))
        if not isinstance(annots, (list, ArrayObject)):
            continue
        for annot_ref in annots:
            annot = deref(annot_ref)
            if not isinstance(annot, DictionaryObject):
                continue
            if annot.get("/Subtype") != NameObject("/Link") and str(annot.get("/Subtype")) != "/Link":
                continue

            action = deref(annot.get("/A"))
            if isinstance(action, DictionaryObject):
                action_type = str(action.get("/S", ""))
                if action_type == "/URI":
                    uri = normalise_metadata_value(action.get("/URI"))
                    external.append({"source_page": page_idx, "uri": uri})
                    continue
                if action_type == "/GoTo":
                    dest_page = resolve_dest_array_to_page(action.get("/D"), page_map)
                    internal.append({"source_page": page_idx, "destination_page": dest_page})
                    continue

            # Link annotations may carry /Dest directly instead of an action dictionary.
            if "/Dest" in annot:
                dest_page = resolve_dest_array_to_page(annot.get("/Dest"), page_map)
                internal.append({"source_page": page_idx, "destination_page": dest_page})

    return internal, external


def normalised_word_counter(path: Path) -> tuple[Counter[str], dict[str, Any]]:
    text = extract_text(str(path)) or ""
    text = unicodedata.normalize("NFC", text).casefold()
    words = TOKEN_RE.findall(text)
    counter = Counter(words)
    return counter, {
        "word_count": sum(counter.values()),
        "unique_word_count": len(counter),
        "character_count_after_extraction": len(text),
    }


def counter_diff(source: Counter[str], master: Counter[str], sample_limit: int = 50) -> dict[str, Any]:
    removed = source - master
    added = master - source
    removed_count = sum(removed.values())
    added_count = sum(added.values())

    def sample(counter: Counter[str]) -> list[dict[str, Any]]:
        return [
            {"word": word, "count": count}
            for word, count in counter.most_common(sample_limit)
        ]

    return {
        "equal": source == master,
        "removed_word_instances": removed_count,
        "added_word_instances": added_count,
        "removed_sample": sample(removed),
        "added_sample": sample(added),
        "sample_truncated": len(removed) > sample_limit or len(added) > sample_limit,
    }


def canonical_counter(items: list[dict[str, Any]]) -> Counter[str]:
    return Counter(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in items)


def compare_nav(source_reader: PdfReader, master_reader: PdfReader) -> dict[str, Any]:
    src_outline = flatten_outline(source_reader)
    mst_outline = flatten_outline(master_reader)
    src_internal, src_external = extract_links(source_reader)
    mst_internal, mst_external = extract_links(master_reader)

    outline_equal = src_outline == mst_outline
    internal_equal = canonical_counter(src_internal) == canonical_counter(mst_internal)
    external_equal = canonical_counter(src_external) == canonical_counter(mst_external)

    return {
        "pass": outline_equal and internal_equal and external_equal,
        "bookmarks": {
            "equal": outline_equal,
            "source_count": len(src_outline),
            "master_count": len(mst_outline),
            "source": src_outline,
            "master": mst_outline,
        },
        "internal_links": {
            "equal": internal_equal,
            "source_count": len(src_internal),
            "master_count": len(mst_internal),
            "source": src_internal,
            "master": mst_internal,
        },
        "external_links": {
            "equal": external_equal,
            "source_count": len(src_external),
            "master_count": len(mst_external),
            "source": src_external,
            "master": mst_external,
        },
    }


def perform_qa(source: Path, master: Path) -> dict[str, Any]:
    try:
        source_reader = PdfReader(str(source), strict=False)
        master_reader = PdfReader(str(master), strict=False)
    except Exception as exc:
        raise Poc4Error(f"Could not open source/master PDF for QA: {exc}") from exc

    # 1. Page count
    source_pages = len(source_reader.pages)
    master_pages = len(master_reader.pages)
    page_check = {
        "pass": source_pages == master_pages,
        "source_page_count": source_pages,
        "master_page_count": master_pages,
    }

    # 2. Text word multiset
    src_words, src_text_stats = normalised_word_counter(source)
    mst_words, mst_text_stats = normalised_word_counter(master)
    text_diff = counter_diff(src_words, mst_words)
    text_check = {
        "pass": bool(text_diff["equal"]),
        "normalisation": "Unicode NFC + casefold + Unicode word-token multiset; order ignored",
        "source_stats": src_text_stats,
        "master_stats": mst_text_stats,
        **text_diff,
    }

    # 3. Descriptive metadata
    src_meta = metadata_snapshot(source_reader)
    mst_meta = metadata_snapshot(master_reader)
    field_results = {
        field: {
            "pass": src_meta[field] == mst_meta[field],
            "source": src_meta[field],
            "master": mst_meta[field],
        }
        for field in METADATA_FIELDS
    }
    metadata_check = {
        "pass": all(v["pass"] for v in field_results.values()),
        "fields": field_results,
        "ignored_technical_fields": ["Producer", "Creator technical changes", "CreationDate", "ModDate", "PDF version"],
    }

    # 4. Tagged state
    src_tagged = is_tagged(source_reader)
    mst_tagged = is_tagged(master_reader)
    tagged_check = {
        "pass": src_tagged == mst_tagged,
        "source_has_struct_tree": src_tagged,
        "master_has_struct_tree": mst_tagged,
        "note": "PoC checks presence/absence of StructTreeRoot only; it does not compare structure-tree semantics.",
    }

    # 5. Navigation
    nav_check = compare_nav(source_reader, master_reader)

    checks = {
        "page_count": page_check,
        "text_word_multiset": text_check,
        "descriptive_metadata": metadata_check,
        "tagged_state": tagged_check,
        "navigational_structures": nav_check,
    }
    flags = [name for name, result in checks.items() if not result.get("pass", False)]
    return {
        "automated_qa_clear": not flags,
        "flags": flags,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DOL Digital Preservation PoC 4 - automated fidelity QA")
    parser.add_argument("--preparation-dir", required=True, type=Path, help="PoC preparation root")
    parser.add_argument("--job-id", required=True, help="Canonical JOB_ID, e.g. 2025_003033")
    args = parser.parse_args()

    try:
        job_id = validate_job_id(args.job_id)
        obj_dir = args.preparation_dir / job_id
        if not obj_dir.is_dir():
            raise Poc4Error(f"Prepared JOB_ID folder not found: {obj_dir}")

        poc2, poc3 = load_records(obj_dir, job_id)
        coverage(obj_dir, {2: poc2, 3: poc3})
        documentation = obj_dir / "documentation"
        conversions = poc2.get("conversions") or []
        validations = poc3.get("validations") or []
        if not conversions or not validations:
            raise Poc4Error("PoC 2/3 records contain no conversion/validation entries")

        conv_by_master_id = {c.get("master_file_id"): c for c in conversions if c.get("master_file_id")}
        conv_by_master_path = {str(Path(c.get("master_path", "")).resolve()): c for c in conversions if c.get("master_path")}

        object_record_path = documentation / f"{job_id}_poc4_qa_record.json"
        qa_entries = load(object_record_path).get("qa_entries", [])
        clear_count = 0
        flagged_count = 0

        for validation in validations:
            if not validation.get("conformance_validated") or validation.get("verdict") != "PASS":
                raise Poc4Error("PoC 3 record contains a non-passing master; refusing to cross the veraPDF gate")

            master = Path(validation["master_path"])
            conv = conv_by_master_id.get(validation.get("master_file_id")) or conv_by_master_path.get(str(master.resolve()))
            if not conv:
                raise Poc4Error(f"Could not map validated master back to PoC 2 conversion: {master}")
            source = Path(conv["source_path"])

            if not source.is_file():
                raise Poc4Error(f"Retained _src source not found: {source}")
            if not master.is_file():
                raise Poc4Error(f"Validated master not found: {master}")

            source_sha512 = sha512_file(source)
            master_sha512 = sha512_file(master)
            if conv.get("source_sha512") and source_sha512.lower() != conv["source_sha512"].lower():
                raise Poc4Error(f"Source fixity changed since PoC 2: {source.name}")
            if validation.get("master_sha512") and master_sha512.lower() != validation["master_sha512"].lower():
                raise Poc4Error(f"Master fixity changed since PoC 3: {master.name}")

            previous = next((q for q in qa_entries if q.get("master_file_id") == validation["master_file_id"]), None)
            if previous:
                verify(previous["qa_report_path"], previous["qa_report_sha512"])
                previous_report = load(previous["qa_report_path"])
                verify(source, previous_report["source"]["sha512"])
                verify(master, previous_report["master"]["sha512"])
                clear_count += previous.get("status") == "AUTO_QA_CLEAR"
                flagged_count += previous.get("status") != "AUTO_QA_CLEAR"
                continue
            qa = perform_qa(source, master)
            base = master.stem
            report_path = documentation / f"{base}_qa_pypdf.json"
            if report_path.exists():
                raise Poc4Error(f"Refusing to overwrite existing QA evidence: {report_path}")

            qa_report_file_id = registered_uuid(obj_dir,"file",str(report_path.resolve())+source_sha512+master_sha512)
            status = "AUTO_QA_CLEAR" if qa["automated_qa_clear"] else "FLAGGED_FOR_REVIEW"
            report = {
                "job_id": job_id,
                "created_at": utc_now(),
                "qa_status": status,
                "governing_principle": "fidelity, not enhancement",
                "source": {
                    "file_id": conv.get("source_file_id"),
                    "path": str(source.resolve()),
                    "sha512": source_sha512,
                },
                "master": {
                    "file_id": validation.get("master_file_id"),
                    "path": str(master.resolve()),
                    "sha512": master_sha512,
                    "verapdf_validated": True,
                    "verapdf_requested_flavour": validation.get("requested_flavour"),
                },
                "qa_report_file_id": qa_report_file_id,
                "automated_checks": qa,
                "human_adjudication": {
                    "required": not qa["automated_qa_clear"],
                    "status": "PENDING" if not qa["automated_qa_clear"] else "NOT_REQUIRED_BY_AUTOMATED_FLAGS",
                    "reviewer": None,
                    "decision": None,
                    "reason": None,
                },
                "relationship": {
                    "type": "isQaReportOf",
                    "report_file_id": qa_report_file_id,
                    "master_file_id": validation.get("master_file_id"),
                },
                "note": (
                    "Automated QA flags differences but never auto-rejects. This PoC does not perform visual review. "
                    "A flagged master must be human-adjudicated before the object can proceed."
                ),
            }
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            report_sha512 = sha512_file(report_path)

            entry = {
                "completed_at": utc_now(),
                "source_file_id": conv.get("source_file_id"),
                "source_path": str(source.resolve()),
                "master_file_id": validation.get("master_file_id"),
                "master_path": str(master.resolve()),
                "qa_report_file_id": qa_report_file_id,
                "qa_report_path": str(report_path.resolve()),
                "qa_report_sha512": report_sha512,
                "status": status,
                "flags": qa["flags"],
                "human_review_required": not qa["automated_qa_clear"],
                "relationship": {
                    "type": "isQaReportOf",
                    "report_file_id": qa_report_file_id,
                    "master_file_id": validation.get("master_file_id"),
                },
            }
            qa_entries.append(entry)
            write_record(object_record_path, {"job_id": job_id, "status": "STEP_4_IN_PROGRESS", "qa_entries": qa_entries})

            if qa["automated_qa_clear"]:
                clear_count += 1
                print(f"[CLEAR] {master.name} automated QA found no differences")
                print(f"        QA report file_id: {qa_report_file_id}")
                print(f"        report: {report_path}")
            else:
                flagged_count += 1
                print(f"[FLAG]  {master.name} requires human review")
                print(f"        checks: {', '.join(qa['flags'])}")
                print(f"        QA report file_id: {qa_report_file_id}")
                print(f"        report: {report_path}")

        object_status = "STEP_4_AUTO_QA_CLEAR" if flagged_count == 0 else "STEP_4_QA_FLAGGED_HOLD"
        object_record = {
            "job_id": job_id,
            "folder_uuid": poc3.get("folder_uuid"),
            "created_at": utc_now(),
            "status": object_status,
            "qa_count": len(qa_entries),
            "clear_count": clear_count,
            "flagged_count": flagged_count,
            "qa_entries": qa_entries,
            "next_action": (
                "Eligible for manual risk-weighted visual sampling / PoC 5 after required review policy is satisfied"
                if flagged_count == 0
                else "HOLD: human adjudication required for all automated flags before continuing"
            ),
            "note": "PoC processing record only; not the future pmsystem event store.",
        }
        object_record_path = documentation / f"{job_id}_poc4_qa_record.json"
        write_record(object_record_path, object_record)

        print(f"\nCompleted: {clear_count} clear, {flagged_count} flagged")
        print(f"PoC 4 record: {object_record_path}")
        return 10 if flagged_count else 0

    except ImportError as exc:
        print(
            "[FATAL] Required Python package missing. Install with:\n"
            "        python -m pip install pypdf pdfminer.six\n"
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    except Poc4Error as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[FATAL] Unexpected error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
