# PREMIS working-profile implementation addendum v1
User-authorized local design, 2026-10-16. Original teammate profile/review is preserved.
This addendum supplies decisions rather than pretending institutional approval.

- Conversion engine is Aspose.PDF for .NET 25.10.0, not C++. creatingApplication
  identifies the engine; digest originators identify the actual script computing
  master/log fixity. The validation script and veraPDF 1.26.2 runtime are distinct.
- UUID identifiers are lowercase UUID4 without prefix; identifier types are plain.
  Controlled labels/attributes are exactly the workbook's 31 Controlled values
  rows, checked automatically. No deprecated linkingAgentRoleEvent scheme is used.
- The working UUID registry is an append-only CSV ledger with persistent JSON
  lookup at the run/repository root. Every new UUID is registered before use.
  The local deployment is serialized; multi-writer production uses a transactional
  registry rather than pretending the CSV provides distributed transactions.
- Exactly two representations are retained. An attachment-only job may have an
  empty rep_1. Multiple same-language sources get unique canonical suffixes;
  notification masters use source UUID paths. Original paths remain in provenance,
  originalName is a leaf filename, and masters never impersonate original files.
- Retain native XML conversion evidence. Before new evidence is hashed, add an
  XML declaration where absent; keep the exact engine report body and its hash.
  Outcome notes quote reported findings, without inventing per-repair claims.
- Retain SQL extraction JSON. DOME creation/revision links both DOME and extract
  as outcomes. Source metadata content, not extraction timestamp, controls reuse.
- Retain unique run-named DROID CSVs. Separate source and post-conversion events
  link the exact files each run read, plus its report outcome. Generic PDF
  identification and veraPDF PDF/A conformance are separate facts; neither is
  falsified to resemble an expected PUID.
- Agent order is first event reference for newly introduced agents. Historical
  agent/object/event elements remain unchanged, with new relationships permitted.
  No human implementer or organization agent is fabricated for an automated run.
- Explicit human adjudication retains automatic evidence, records exact evidence
  hashes and a configured reviewer, and emits a successful manual qualityReview
  only on acceptance, with implementer role and the decision as an outcome.
  Reject decisions remain held; all PDF/A/fixity gates still apply.
- Explicit reconversion retains the source UUID, produces a fresh master UUID at
  a distinct preservation path, records normalization/validation/QA anew and
  creates an incremental master_reconversion version. Prior masters and reports
  remain unchanged. Stable request tokens prevent duplicate reprocessing.
- Already conformant inputs are not falsely recorded as normalized by Aspose.
  Copy/skip status remains in evidence; creatingApplication is omitted for a
  skipped conversion. The source remains described for provenance in this local
  extension to the profile's unresolved conversion-skipped case.
- Operation completion timestamps are captured at source preparation, conversion,
  validation, QA, DOME/extraction creation and report creation. Missing legacy
  precision is not fabricated and historical committed metadata is not rewritten.
- DOME remains the supplied pinned v0.3 schema. Unsupported crosswalk terms remain
  in source evidence pending a schema revision. CatalogueStatus is a workflow
  state; CatStatus supplies editorial DOME status.
- Local selection/restriction, METS and registry policies are specified in
  WORKFLOW_v3_DESIGN.md, METS_PROFILE_v1.md and AGENT_REGISTRY_DESIGN.md.
  These are the explicit dispositions of W01?W17. Teammate signoff is separate
  from code implementation and automated validation.
