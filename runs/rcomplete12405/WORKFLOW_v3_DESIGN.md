# DPR workflow v3 ? implemented local design
Status: user-authorized working design, 2026-10-16; not institutional approval.
This document supersedes contradictory PoC implementation notes for new profile runs.
Original Workflow Documentation v2.5 and PREMIS profile remain preserved references.

W01/W02 ? Compare source bytes and SQL metadata content with preceding records;
reuse unchanged source/master/validation/QA evidence. File IDs identify content
instances, not language slots. Multiple DOL files per language and multiple
notification PDF attachments are supported. Word remains DOL-only. A genuine
metadata change writes a new DOME and extraction file and a revision event.
No changed content means no SIP or OCFL version. Source arrivals never silently
remove earlier logical paths.

W03/W04 ? Read PREMIS at the actual repository head and append. Superseded file
objects, prior events and agents remain unchanged. Correct erroneous facts by
DPM114 with recorded/correct values and reason, never edit committed history.
Unchanged UUID/content is carried forward; new bytes receive a new UUID4.

W05 ? Source DROID before conversion supplies received-file evidence.
Post-conversion DROID identifies new carried files including masters, reports
and extraction evidence; DOME has the profile's fixed XML identification.
Each DROID event links the files read by its own report and the report outcome.
XML evidence created by the converter receives an XML declaration before fixity;
the engine body is unchanged and its body SHA-512 is recorded in driver output.
Ambiguous or missing identification remains a hold in strict profile mode.

W06 ? Nine bag-info fields: Source-Organization, SIP External-Identifier, JOB_ID
Internal-Sender-Identifier, External-Description, batch Bag-Group-Identifier,
Bagging-Date, Payload-Oxum, DPR-Object-UUID and predecessor OCFL head/none.
Independent manifest, identity, schema and predecessor gates pass before commit.
The received SIP is immutable. Step 8 produces a separate hash-verified PREMIS/
METS snapshot; Step 9 incorporates it and appends AIP creation. Failed gates
commit nothing and preserve evidence.

W07/W08 ? Root METS; metadata/descriptive/DOME; metadata/preservation/PREMIS;
rep_1 authoring sources; rep_2 masters and attachment originals. The first DOL
file retains JOB_LANG names; additional files receive stable unique suffixes.
Attachment masters use source UUID filenames; original filenames are recorded
in PREMIS. DOME carries catalogue description, not preservation file inventories.

W09 ? Retain the SQL extraction as provenance and describe it in PREMIS.
The DOME creation/revision event links both DOME and extraction as outcomes.
Compare metadata content rather than extraction timestamps. The supplied
completed Crosswalk sheet governs extraction. Missing schema terms remain
in source evidence, not invalid XML. CatalogueStatus is a workflow state,
so editorial CatStatus remains DOME status. Raw CatalogueStatus is retained.

W10/W11 ? Scope is restriction IDs 44/91. Restricted material is held.
A first public/derestricted arrival is new_document v1, with actual restriction
and derestriction data in DOME. Local action mapping is explicit: NEW allows a
new document or new language/rendition; REPUBLISH_MODIFIED and DMS_DOC_CHANGE
allow changed files/language/rendition; DMS_META_DATA_CHANGE allows metadata-only
change. Cancellation/wrong-path/unknown combinations hold. Content controls
no_change. Existing restricted-to-public metadata transitions at restriction44
use derestriction. Explicit correction/reconversion bases require actual changes;
a selection-basis label alone never forces conversion or waives QA.
Reconversion remains an explicit operator operation; it must create fresh
outputs/evidence and pass every gate, not reuse a successful conversion record.

W12 ? See AGENT_REGISTRY_DESIGN.md. Actual engine is Aspose.PDF for .NET 25.10.0.
W13 ? This complete design records the local dispositions of W01?W17.
W14 ? Retain unique per-run DROID report names already implemented; run identity
is chosen instead of a repository-version filename. Names remain unique across
incremental arrivals, and earlier reports remain in complete OCFL state.
W15 ? See the append-only UUID CSV ledger and persistent JSON lookup.
W16 ? Supplied DOME v0.3 remains pinned and validated. CRNid, TraslationRule and
derestrictionType have no schema element; actual values remain in extraction.
A future institution-approved DOME schema may add them; none is invented here.
W17 ? See METS_PROFILE_v1.md and executable mets_profile.py.

QA policy ? A fidelity difference holds the item. An automated pass is not
manual review, so no implementer is invented. Human adjudication must be recorded
as a separate supplied decision with reviewer identity, evidence hashes and
reason before a hold can be released; the automated report stays unchanged.
The present CLI does not release human-adjudicated holds automatically.

Storage ? OCFL 1.1, SHA-512, append-only versions. Root inventory equals latest
inventory; every historical inventory retains history through its own head.
version-summary.json describes only that version and is covered by METS/OCFL.
Independent ocfl-py evidence is kept outside the object to avoid self-referential
validation evidence. It accompanies the example as a separate review artifact.

Operational boundaries ? CSV registry uses a single writer. Incremental commit
checks head under a lock but inventory and sidecar are separate filesystem writes;
interrupted operations hold for manual recovery rather than silent overwrite.
Production multi-writer registry transactions, automatic manual-QA release and
automatic interrupted-commit recovery are not asserted by this design.
