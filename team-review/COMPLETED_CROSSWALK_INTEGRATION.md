# Completed crosswalk integration ? 2026-10-16

Source: DOL_DOME_crosswalk_COMPLETED_2026-10-16.xlsx, sheet Crosswalk.
The original supplied workbook is retained unchanged. completed_crosswalk.json records its SHA-512 and every row. V.1 is the historical sheet and is not used for SQL mappings. Embedded notes are reference material, not instructions to execute.

Profile extraction now resolves mapped database columns through verified tables and keys. The legacy non-profile SQL/writer remains unchanged. Existing packages are not rewritten.

Implemented: DMS-form jobID; CreateDate documentDate; per-language LCID/language, NbPages/pages and PostingDate/dateIssued; AccessType/accessRights separate from RestrictionType/ACLInfo; MetaCollection label/collection; DerestrictionDate; requirement long/short names, measures, periodicity and notes; notification objective/topic, affected/notifying countries; typed HS/ICS/Services/Environment coverage; repeated attachment FileURL references with language and filename. Existing symbol, title, source, subject, body, country, article, product and version vocabulary joins remain in place.

Live read-only verification: job 12405.26. Output is crosswalk-live-check-12405/dome.xml; raw/provenance extraction is crosswalk-live-check-12405/extraction.json. DOME validates against the unchanged pinned v0.3 schema.

Unresolved discrepancies:
- CRNid, TraslationRule (workbook spelling) and derestrictionType are absent from DOME v0.3. Real source values are retained in extraction provenance, not emitted as invalid XML.
- Workbook status/CatalogueStatus is CatalogueDetail's per-language workflow state. Existing CatStatus editorial labels are retained; raw CatalogueStatus values are recorded by language pending agreement on intended semantics.
- FileURL contains relative references for 12405.26. These are preserved as source values accepted by xs:anyURI; no external base URL is invented.
- Requirement topic follows the workbook's ObjectiveNoteListID mapping. Its live value is Environmental protection; this is not a ReqSubject/CRNSubject mapping.
- Format/extent have no SQL source in the workbook; per-file format and size remain in PREMIS.
- mdDateCreated/mdDateModified retain pipeline DOME creation/revision semantics. The workbook still leaves their source choice open.
- Vocabulary labels are emitted from existing verified tables. Authoritative URI crosswalks are not supplied.
- Crosswalk completion does not approve the provisional operator/software registry or the whole preservation profile.

Next schema/profile revision must explicitly settle the unsupported elements and status semantics. Existing AIPs, UUIDs, versions and preservation packages are retained.
