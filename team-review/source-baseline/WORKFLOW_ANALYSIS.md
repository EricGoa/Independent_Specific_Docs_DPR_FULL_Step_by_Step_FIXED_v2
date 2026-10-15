# Workflow dependency analysis and implementation

Inspected the Python stages, monitor, command guide, .NET converter source/project,
DOME schema, input/preparation layouts and existing JSON records before editing.
No existing tests or dependency manifest were present. The repository initially
contained only untracked project files. Existing input and preparation content
was read only; no production stage was executed.

| Stage | Inputs and dependencies | Output / downstream contract |
| --- | --- | --- |
| specific_step1 | SQL Server, pyodbc, ODBC driver, accessible DOL/notification drives | CSV plus immutable input receipts; original paths and document kind |
| PoC1 | CSV and received PDF/Word files; Python standard library | Original file UUIDs, object UUID, SHA-512, staged files and processing record |
| PoC5 / DROID | PoC1 files; Java and DROID JAR | Source characterization, raw CSV, stable source IDs and evidence IDs |
| PoC2 / Aspose | PoC1 PDF records; .NET 8, compiled converter DLL and Aspose licence | Separate PDF/A master UUIDs, original-to-master links, SHA-512 and conversion evidence |
| PoC3 / veraPDF | PoC2 conversions; veraPDF launcher | Explicit conformance verdict and report ID/hash for every master |
| PoC4 / QA | PoC2 and passing PoC3 records; pypdf and pdfminer.six | Source/master fidelity checks, clear/hold verdict and report ID/hash |
| PoC6 / metadata | Complete PoC1-5 coverage; SQL, lxml, local DOME XSD | DOME, PREMIS and METS with paths, identifiers, fixity and derivation |
| PoC7 / BagIt | Current PoC6 metadata and complete upstream coverage | Independently validated SHA-512 bag; preparation remains intact |
| PoC8 / ingest | Bag contents, independently of PoC7 | Exact verified payload paths/digests and object identity |
| PoC9 / OCFL | Exact PoC8-verified bag, existing OCFL history if present | Verified v1 or append-only forward delta, version inventories and sidecars |
| Monitor | Stage records and deterministic AIP location | Excel/read-model JSON; historical filenames/status strings remain compatible |

The logical order is 1 -> 5 -> 2 -> 3 -> 4 -> 6 -> 7 -> 8 -> 9.
PoC numbering and successful stage status strings are preserved.

The initial gaps were attachment exclusion in conversion, a single DOL filename
per language, overwritable input copies, basename-based source matching, rerun
refusal in conversion/validation/QA, destructive DROID evidence reuse, stale
PASS records after arrivals, missing attachment DOME paths, missing PREMIS
source/master relationships, v1-only OCFL, mismatched command-guide job IDs,
and an invalid literal backslash-n terminator in monitor JSON.

The implementation plan was provided in chat before edits: retain legacy DOL
names and identities, use PoC1 as the authoritative source inventory, add safe
incremental reuse and record revisions, enforce complete downstream coverage,
include attachment provenance in metadata and payloads, append OCFL versions,
and verify the changes with synthetic regressions. Those changes are implemented.

## Compatibility and operating instructions

- The first DOL pair for a language retains JOB_LANG.docx and JOB_LANG_src.pdf.
  Additional pairs or changed receipts receive a deterministic suffix. Existing
  IDs, original records, and staged bytes are retained. DOL authoring files must
  be .doc or .docx, and notification attachments must be .pdf.
- The original four CSV columns remain supported. New optional columns are
  document_kind, pdf_original_path and source_original_path. Explicit
  document_kind=notification supports PDF-only rows without filename conventions.
  Legacy notification filenames still work. Unknown attachment language is und;
  the local DOME schema now permits that value.
- Legacy attachment originals stay at their existing paths. New attachment
  masters are separate files under attachments/preservation/<source-file-id>.pdf,
  with a distinct master UUID in the conversion record. A filename ending in
  _src.pdf is still retained if it is a notification attachment.
- poc5_droid.py is the canonical importable script. poc5_droid-Copy.py remains
  a compatible command entry point. DROID processes new receipts on incremental
  runs and merges earlier characterization; previous raw exports remain intact.
- Rerun stages in logical order after new arrivals. Conversion, validation and
  QA reuse matching files after checking recorded SHA-512. Successful work is
  checkpointed so a later conversion failure does not discard completed records.
  Metadata is reused when SQL values, source coverage and output fixity match.
- Superseded JSON records and metadata snapshots are retained in record-history.
  Working history is excluded from payload packages. Retained PREMIS records the
  identities and original paths of DOL working PDFs even though the existing
  DOL policy excludes their _src copies from the bag.
- An identical staging package is reused. A changed package requires a fresh
  --staging-dir (for example staging/batch_002), used consistently for PoC7-9.
  Pass the same explicit --ingest-records-dir to PoC8 and PoC9. The deprecated
  --replace-existing flag cannot delete an existing bag. Old packages stay intact.
- OCFL retains all previous version directories and logical paths. Incoming
  metadata may update the current logical path while prior bytes stay in earlier
  versions. A receipt absent from a later package is retained. Identical logical
  state creates no additional version. Content is deduplicated by SHA-512.

## Validation

Run with a Python environment containing pypdf, pdfminer.six and lxml:

    python -m unittest discover -s tests -v

The suite covers EN/FR/ES DOL pairs, multiple pairs per language, multiple
notifications, unknown language, legacy records, duplicate basenames with distinct
origins, changed receipts, idempotent reruns, incremental v2 and byte-identical v1,
partial conversion failure/resume, QA page-count differences, fixity tampering,
explicit veraPDF verdicts, disabled package replacement, unsafe manifest paths,
and valid monitor JSON. PDFs are generated by pypdf and Word containers are
synthetic ZIP fixtures. External SQL, Aspose, DROID and veraPDF calls are mocked;
PDF QA, XML/XSD validation, BagIt verification and OCFL operations execute locally
in temporary directories. Mocked conversion is a copy and does not claim real
PDF/A conversion or conformance. All project Python sources are also compiled
in memory for syntax validation.

## Practical limits

Live SQL permissions/schema, Aspose licensing, actual DROID output, and veraPDF
execution still require an operator smoke test using a separate non-production
job. Legacy records that only recorded an input path cannot reconstruct a lost
upstream original path; new inventories capture the original drive path.

PREMIS and METS remain the project's existing metadata projections, extended
with identifiers and derivation. No external PREMIS/METS schemas or institutional
crosswalk were supplied, so full standards/schema conformance is not asserted.
DOME is validated against the supplied local schema. The OCFL validator checks
the implemented inventory/version/fixity features; it is not an independent full
OCFL conformance validator.

OCFL writers use an exclusive lock for delta commit and preserve workspaces.
The root inventory and its sidecar are replaced separately; a process interruption
can require manual recovery from the retained workspace/version inventory. The
validator fails closed on an inconsistent object. No automated cleanup deletes
an existing OCFL version or staging package. Preparation should have one writer
per job; parallel writers to the same job are not supported.
