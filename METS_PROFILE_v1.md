# DPR PoC METS profile v1
Status: user-authorized working design, 2026-10-16. Institutional review remains required.
Identifier: urn:dpr:poc:mets-profile:1. Executable rules: mets_profile.py.

METS uses the pinned local METS 1 schema. PREPARATION identifies the object UUID;
SIP OBJID identifies this SIP UUID; AIP OBJID is urn:uuid: plus object UUID.
metsHdr records creation, World Trade Organization as creator organization,
JOB_ID as altRecordID and the originating SIP identifier on AIPs.
No invented staff identity or authority URI is introduced.

Exactly one DOME dmdSec/ID DOME references metadata/descriptive/JOB_ID_dome.xml.
Exactly one amdSec/digiprovMD/ID PREMIS references metadata/preservation/JOB_ID_premis.xml.
References carry SHA-512, byte size, application/xml and relative xlink:href.
DOME uses MDTYPE OTHER and OTHERMDTYPE DOME; preservation uses PREMIS.

fileSec lists every carried payload file except METS itself. Every entry has a
unique local ID, byte size, SHA-512 and one safe relative FLocat URI. No absolute
filesystem paths, parent traversal or duplicate payload locations are allowed.
The physical structMap has one intellectualEntity div linked to DOME/PREMIS,
two representation divs rep_1/rep_2 and one documentation div. Every file pointer
resolves exactly once; files under representations belong to their correct div.
Notification originals and PDF/A derivatives reside within rep_2. Metadata,
schemas and version-summary.json are administrative files in documentation.
An attachment-only object may have an empty rep_1; this is the explicit local
extension to the two-representation profile. METS describes the carried package;
OCFL inventory describes complete version state.

Step 6 writes preparation METS. Step 7 writes SIP METS after SIP PREMIS creation.
Step 8 rebuilds METS in a separate verified snapshot after its PREMIS fixity event;
the sealed SIP remains unchanged. Step 9 writes AIP METS after its PREMIS creation
event and version-summary.json. The summary and PREMIS are both checksum-covered.
METS cannot checksum itself. OCFL supplies METS fixity.

Generation, ingest and final example validation enforce the executable profile.
Schema-valid but structurally incorrect METS is rejected. Existing committed
METS files are never rewritten; older pending bags without this profile identifier
must be rebuilt as a new SIP rather than edited in place.
