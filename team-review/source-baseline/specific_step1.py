
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from workflow_common import input_copy
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def dms(job):
    try:
        y, s = job.split("_", 1)
        if len(y) != 4 or not y.isdigit() or not s.isdigit():
            raise ValueError
        return f"{int(s)}.{y[-2:]}"
    except Exception:
        raise SystemExit(
            "[ERROR] JOB_ID must be YYYY_NNNNNN, e.g. 2026_011604"
        )


def lang(path):
    p = str(path).upper()
    if p.startswith(("T:\\", "Q:\\")):
        return "en"
    if p.startswith(("U:\\", "R:\\")):
        return "fr"
    if p.startswith(("V:\\", "S:\\")):
        return "es"
    return ""


def attachment_lang(path):
    """Infer language from filename suffix when available."""
    stem = Path(path).stem.lower()

    if stem.endswith("_e"):
        return "en"
    if stem.endswith("_f"):
        return "fr"
    if stem.endswith("_s"):
        return "es"

    return "und"


def normalize_fileurl(value):
    """Convert relative notification URL to F: drive path."""
    if value is None:
        return None

    value = str(value).strip().replace("/", "\\")

    if not value:
        return None

    if value.upper().startswith("F:\\"):
        return Path(value)

    return Path("F:\\" + value.lstrip("\\"))


def main():
    ap = argparse.ArgumentParser(
        description="Independent DPR specific-document Step 1"
    )
    ap.add_argument("--job-id", required=True)
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent
    )
    ap.add_argument("--server", default="Pekan.ext.wto, 14133")
    ap.add_argument("--database", default="DOL2DB")
    ap.add_argument("--driver", default="ODBC Driver 17 for SQL Server")

    a = ap.parse_args()

    root = a.root.resolve()
    input_dir = root / "input"
    prep = root / "preparation"
    runs = root / "specific-workflow-runs"

    for folder in (input_dir, prep, runs):
        folder.mkdir(parents=True, exist_ok=True)

    try:
        import pyodbc
    except ImportError:
        raise SystemExit("[ERROR] pyodbc is required.")


    dj = dms(a.job_id)

    sql = """
    SELECT
        cd.FileName,
        cd.CatDetailId,
        ch.DmsJobId,
        'F:\\' + REPLACE(na.FileURL, '/', '\\') AS FileURL
    FROM dbo.CatalogueHeader ch
    LEFT JOIN dbo.CatalogueDetail cdet
        ON cdet.CatHeaderID = ch.Id
    LEFT JOIN dbo.CatDocument cd
        ON cd.CatDetailId = cdet.ID
    LEFT JOIN dbo.CatHeaderNotification chn
        ON ch.Id = chn.CatHeaderID
    LEFT JOIN dbo.NotAttachment na
        ON chn.NotificationID = na.NotificationID
        AND na.Deleted = 0
    WHERE ch.DmsJobId = ?
      AND ch.RestrictionType IN (44, 91)
    ORDER BY ch.DmsJobId, cd.FileName;
    """

    cs = (
        f"DRIVER={{{a.driver}}};"
        f"SERVER={a.server};"
        f"DATABASE={a.database};"
        "Trusted_Connection=yes;"
    )

    print(f"[JOB_ID] {a.job_id}")
    print(f"[DMS JOB ID] {dj}")
    print("[QUERY] DOL2DB")

    try:
        with pyodbc.connect(cs) as c:
            rows = c.cursor().execute(sql, dj).fetchall()
    except Exception as e:
        raise SystemExit(f"[ERROR] DOL2DB query failed: {e}")

    if not rows:
        raise SystemExit(
            f"[ERROR] No documents found for {dj} "
            "with RestrictionType 44/91."
        )


    # --------------------------------------------------
    # 1. Collect unique DOL files and notification files
    # --------------------------------------------------

    dol_paths = {}
    attachment_paths = {}

    for r in rows:

        if r.FileName:
            p = Path(str(r.FileName))
            dol_paths[str(p).lower()] = p

        if r.FileURL:
            p = normalize_fileurl(r.FileURL)
            if p:
                attachment_paths[str(p).lower()] = p

    print(f"[DOL] Unique files: {len(dol_paths)}")
    print(
        "[NOTIFICATION] Unique attachments: "
        f"{len(attachment_paths)}"
    )


    # --------------------------------------------------
    # 2. Process DOL PDF + authoring source pairs
    # --------------------------------------------------

    groups = defaultdict(lambda: {"pdf": [], "source": []})

    for source in dol_paths.values():

        lg = lang(source)

        if not lg:
            print(f"[SKIP] Unknown language drive: {source}")
            continue

        if source.suffix.lower() not in {".pdf", ".doc", ".docx"}:
            continue

        if not source.is_file():
            print(f"[ERROR] Source not accessible: {source}")
            continue

        key = (lg, source.stem.lower())

        if source.suffix.lower() == ".pdf":
            groups[key]["pdf"].append(source)
        else:
            groups[key]["source"].append(source)


    inventory = []

    for (lg, stem), g in sorted(groups.items()):

        if not g["pdf"] or not g["source"]:
            print(
                f"[SKIP] Incomplete pair {lg}/{stem}: "
                f"pdf={len(g['pdf'])}, "
                f"source={len(g['source'])}"
            )
            continue

        pdf = g["pdf"][0]
        author = g["source"][0]

        pdf_dest = input_dir / f"{a.job_id}_{lg}_{pdf.name}"
        src_dest = input_dir / f"{a.job_id}_{lg}_{author.name}"

        pdf_dest = input_copy(pdf, pdf_dest)
        src_dest = input_copy(author, src_dest)

        print(f"[COPIED] {lg} PDF: {pdf}")
        print(f"[COPIED] {lg} SRC: {author}")

        inventory.append({
            "job_id": a.job_id,
            "language": lg,
            "pdf_file": pdf_dest.name,
            "source_file": src_dest.name,
            "pdf_original_path": str(pdf),
            "source_original_path": str(author),
            "document_kind": "dol"
        })


    # --------------------------------------------------
    # 3. Process notification attachments independently
    # --------------------------------------------------

    attachment_count = 0

    for attachment in sorted(
        attachment_paths.values(),
        key=lambda p: str(p).lower()
    ):

        if not attachment.is_file():
            print(
                "[ERROR] Notification attachment "
                f"not accessible: {attachment}"
            )
            continue

        if attachment.suffix.lower() != ".pdf":
            print(f"[SKIP] Notification attachment is not PDF: {attachment}")
            continue
        lg = attachment_lang(attachment)

        # Prefix to distinguish notification attachments
        # from ordinary DOL files.
        dest = input_dir / (
            f"{a.job_id}_notification_{lg}_{attachment.name}"
        )

        dest = input_copy(attachment, dest)

        print(
            f"[COPIED] NOTIFICATION ({lg}): "
            f"{attachment} -> {dest}"
        )

        # PDF-only attachment row.
        # PoC1 must support an empty source_file.
        inventory.append({
            "job_id": a.job_id,
            "language": lg,
            "pdf_file": dest.name,
            "source_file": "",
            "pdf_original_path": str(attachment),
            "source_original_path": "",
            "document_kind": "notification"
        })

        attachment_count += 1


    print(
        f"[SUMMARY] DOL pairs: "
        f"{len(inventory) - attachment_count}"
    )
    print(
        f"[SUMMARY] Notification attachments: "
        f"{attachment_count}"
    )


    # --------------------------------------------------
    # 4. Save inventory
    # --------------------------------------------------

    if not inventory:
        raise SystemExit(
            "[ERROR] No accessible documents or "
            "notification attachments found."
        )

    csv_path = runs / (
        f"{a.job_id}_"
        f"{datetime.now():%Y%m%d_%H%M%S}_inventory.csv"
    )

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        w = csv.DictWriter(
            f,
            fieldnames=[
                "job_id",
                "language",
                "pdf_file",
                "source_file", "pdf_original_path", "source_original_path", "document_kind"
            ]
        )

        w.writeheader()
        w.writerows(inventory)

    print(f"[INVENTORY] {csv_path}")


    # --------------------------------------------------
    # 5. Run PoC1 preparation
    # --------------------------------------------------

    cmd = [
        sys.executable,
        str(root / "poc1_builder.py"),
        "--csv",
        str(csv_path),
        "--input-dir",
        str(input_dir),
        "--preparation-dir",
        str(prep)
    ]

    print("[PREPARATION] Running PoC1 for this JOB_ID only.")

    raise SystemExit(
        subprocess.run(cmd, cwd=root).returncode
    )


if __name__ == "__main__":
    main()
