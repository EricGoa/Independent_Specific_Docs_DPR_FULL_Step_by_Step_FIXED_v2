> Latest implementation update (2026-10-16): see WORKFLOW_v3_DESIGN.md, METS_PROFILE_v1.md, AGENT_REGISTRY_DESIGN.md and PREMIS_PROFILE_IMPLEMENTATION_ADDENDUM_v1.md. The completion workbook records every F001?F067 and W01?W17 disposition. Final real example: runs/rverified12405/FINAL_RESULT.html. Human QA and explicit reconversion are now implemented; all 39 tests passed. Earlier partial-work notes below are historical.

# Team review implementation — 2026-10-15

The supplied reviews and PREMIS profile are reference requirements, not authority
to edit production packages, contact teammates, or rewrite immutable OCFL history.
The source documents are preserved unchanged under `team-review/`; the original
files in Documents and H: were not modified. Source snapshots from before this
upgrade are in `team-review/source-baseline/`.

## Implemented operating path

Use PoC6 `--profile` to select the new draft-profile path. The old DOL processing
and local metadata implementation remain available for compatibility. Legacy
bags require explicit `--allow-legacy-package` at PoC8 and PoC9; their identities
are never silently reinterpreted as the new scheme.

The profile path supports ordinary DOL documents and multiple PDF attachments.
It writes DOME to `metadata/descriptive/JOB_dome.xml`, PREMIS to
`metadata/preservation/JOB_premis.xml`, METS 1 to `JOB_mets.xml` at the payload
root, and packages offline schemas in `schemas/`.

Both supplied DOME XSD files are byte-identical. The profile path uses those
bytes and their namespace; the earlier `-poc.xsd` is retained only for legacy
processing. DOME no longer carries a preservation file list. METS describes the
payload, DOME and PREMIS references, and the two representations. DOME titles
use xml:lang. Descriptive bytes and the SQL extract are reused if SQL values are
unchanged, even when files arrive. CatHeaderId is now selected explicitly.

PREMIS uses the default PREMIS 3 namespace, UUID and filepath identifiers,
intellectual entity and representation objects, compositionLevel, originators,
PRONOM fields from DROID, registered agents, derivation/dependency relationships,
and the event catalogue. Object/source/master/report IDs already minted by the
stages are reused. UUID4s are registered before use; collisions halt processing.
A local append-preserving registry is under `.registries/uuid-registry.json`.
It is a provisional implementation of the registry design, not the missing
institutional registry. Run one workflow writer at a time: this JSON registry
is not a multi-process database.

The original DOL published PDFs are described without filepath identifiers;
the existing packaging rule still omits their transient working copies. Original
notification PDFs remain held separately from their masters. Multiple files per
language and unknown-language attachments are retained; undetermined language
and attachment-only empty rep_1 are profile adaptations requiring team review.

PoC6 can perform post-conversion DROID characterization with `--java` and
`--droid-jar`, including new masters and evidence. Missing or ambiguous PUIDs
are explicit draft gaps. MIME types are never substituted for PRONOM names.
The source-only PoC5 run remains an independent pre-conversion check.

Each SIP has a registered SIP UUID, independent of JOB_ID/object UUID/batch UUID.
The nine requested bag-info fields are written exactly with their new meanings.
The SIP METS OBJID is the SIP UUID; the AIP METS OBJID and OCFL inventory id use
urn:uuid:OBJECT_UUID. AIP METS retains the SIP identifier as altRecordID TYPE=SIP.

PoC8 checks manifests, all identifier agreements, schemas, METS references, and
repository predecessor. PoC9 repeats those checks against the exact PoC8-verified
SIP, copies it to a separate workspace, appends DPM112/DPM113 and needed agents,
checks append-only history and PREMIS/payload/OCFL history agreement, rewrites
AIP METS together with PREMIS, validates (including a semantic-unit allowlist and UUID4 checks), and only then commits. The received
bag remains byte-identical. A stale competing SIP is held; no version is committed.
Verification records are retained per SIP as well as at the monitor's legacy path.

Each commit names the configured authorizer and mailto address, starts its message
with the SIP's selection_basis and an em dash, and has a timestamp no earlier
than AIP creation. Those facts are cross-checked before commit. Failed attempts
produce hold records. Existing v1/v2/etc. directories are never rewritten.
Legacy bare-UUID inventory IDs are rejected by profile mode rather than edited.

A proposed selection basis is calculated from newly described files/languages
and changed DOME content; `--selection-basis` provides an explicit override.
The DOL LastDmsAction vocabulary has not been supplied or verified, so these are
content-based proposals, not a claim to implement its institutional routing rules.
`premis_correction` can be used with PoC6 `--corrections FILE.json`; corrections
append DPM114 and never edit the mistaken element. Explicit master-reconversion
routing and manual QA adjudication remain open work.

## Independent OCFL validation

`validate_ocfl.py` wraps the supplied upstream read-only validator, pinned under
`vendor/ocfl-py/` with its commit recorded in SOURCE.json. The upstream source is
unmodified. The wrapper adapts Windows path separators inside upstream OCFL
modules because fsspec returns POSIX paths; no validation rules or digest checks
are disabled. Pairtree is also pinned with its upstream license/source metadata.

At PoC9 use `--ocfl-validator validate_ocfl.py --require-independent-validator`.
If the validator's dependencies are in another environment, supply
`--validator-python PATH_TO_PYTHON`. In this workspace the base Anaconda Python
has bagit/fsspec/s3fs and successfully runs the independent validation. Direct
PyPI installation timed out, so no global environment was altered.

The core manual BagIt implementation remains RFC-compatible; the table's
Bagging-Date and Payload-Oxum are calculated locally rather than falsely claiming
that bagit-python generated them. The upstream validator's bagit import is a
dependency, not the producer of our SIP tags.

## Draft boundaries and review responses

`preservation_config.json` is explicitly draft. Its operator is
Synthetic Preservation Operator / preservation-test@example.invalid, not a
WTO staff member. Aspose.PDF for .NET 25.10.0 is confirmed from the converter
project. Stage versions are source SHA-256 fingerprints, not invented release
tags. DROID/tool/signature configuration and approved production person-agent
rows still need to be supplied. Strict PoC6/PoC9 rejects the provisional registry;
strict PoC6 also rejects missing PRONOM identification and CatHeaderId.

The completed crosswalk supplied on 2026-10-16 supersedes the earlier
provisional workbook: team-review/DOL_DOME_crosswalk_COMPLETED_2026-10-16.xlsx,
Crosswalk sheet. Profile extraction now implements verified SQL mappings,
including notification fields. See team-review/COMPLETED_CROSSWALK_INTEGRATION.md
for live validation and remaining schema/status conflicts. The original Amexio
schema and current workflow document are still not supplied locally.
Existing preservation packages are unchanged.

`team-review/2026_011922_premis_conformance_review_implementation.xlsx` preserves
the original workbook and adds responses for all F001–F067. Implemented findings
are marked In progress pending teammate re-review, not unilaterally Closed.
Partial implementations and unresolved decisions are explicitly identified on
its Implementation sheet. The following workflow dispositions apply:

| Item | Disposition |
| --- | --- |
| W01/W02 | Reuse unchanged files and SQL/DOME; content comparison, not timestamp-only comparison |
| W03/W04 | Repository-head PREMIS carry-forward and correction by addition; no historical rewrites |
| W05 | Source check retained; optional post-conversion characterization of new evidence |
| W06 | Nine bag-info fields and all identity/predecessor ingest gates implemented |
| W07 | Profile layout, METS 1, stable canonical DOL names; multiple-file/attachment extensions documented |
| W08 | File list removed from DOME; changed DOME gets revision provenance |
| W09 | SQL extract retained by proposal; stable content reused; exact outcome-event policy remains open |
| W10 | Content-based selection proposal plus override; authoritative LastDmsAction mapping pending |
| W11 | Existing 44/91 restriction selection preserved; restriction/derestriction policy not invented |
| W12 | Provisional registry and actual .NET engine; production registry/tool versions pending |
| W13 | This implementation note records changes; institutional workflow revision still needs team approval |
| W14 | Unique DROID evidence retained; final approved naming still open |
| W15 | UUID4 local registry implemented; production registry location/backend approval pending |
| W16 | Completed crosswalk integrated; v0.3 schema gaps and CatalogueStatus semantic conflict documented |
| W17 | METS 1 schema validation and descriptive/preservation sections implemented; final METS business profile still pending |

This is a working draft implementation, not a declaration that all 67 review
findings or all institutional profile requirements are closed. Other remaining
items include extraction-log event outcome links, parsing the converter's exact
alteration notes, recovery after interrupted inventory/sidecar replacement,
and proving real tool behavior against
an operator-supplied test job. Existing production packages were not run, deleted,
overwritten, or re-ingested during this work.

## Final verification

27 automated tests passed; all 18 project/test Python files compile. The saved
example under examples/synthetic-profile-v1-v2 includes unchanged SIP bags and
an independently validated v1/v2 OCFL object. SQL/Aspose/DROID/veraPDF calls were
mocked; XML/XSD, BagIt, PREMIS history/identity, and independent OCFL checks ran
for real. See team-review/TEST_RESULTS.json and VALIDATION_RUNTIME.json.
