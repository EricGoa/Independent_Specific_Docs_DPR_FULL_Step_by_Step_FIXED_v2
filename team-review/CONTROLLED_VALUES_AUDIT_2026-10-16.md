# Controlled values audit ? 2026-10-16

All 31 Controlled values rows have matching label/authority/URI definitions in the profile writer. This is vocabulary support, not proof that all required scenarios or review findings are resolved.

| Element | Value | Writer support | Example occurrences | Example check |
|---|---|---|---:|---|
| messageDigestAlgorithm | SHA-512 | True | 32 | PASS |
| formatRegistryRole | specification | True | 27 | PASS |
| relationshipType | structural | True | 24 | PASS |
| relationshipType | derivation | True | 5 | PASS |
| relationshipType | dependency | True | 15 | PASS |
| relationshipSubType | includes | True | 12 | PASS |
| relationshipSubType | is Included In | True | 12 | PASS |
| relationshipSubType | has Source | True | 5 | PASS |
| relationshipSubType | isValidationReportOf | True | 5 | PASS |
| relationshipSubType | isQaReportOf | True | 5 | PASS |
| relationshipSubType | isConversionLogOf | True | 5 | PASS |
| eventType | ingestion start | True | 1 | PASS |
| eventType | message digest calculation | True | 13 | PASS |
| eventType | fixity check | True | 9 | PASS |
| eventType | filename change | True | 5 | PASS |
| eventType | normalization | True | 5 | PASS |
| eventType | validation | True | 5 | PASS |
| eventType | qualityReview | True | 5 | PASS |
| eventType | format identification | True | 1 | PASS |
| eventType | creation | True | 1 | PASS |
| eventType | metadata modification | True | 0 | NOT PRESENT (scenario dependent) |
| eventType | information package creation | True | 2 | PASS |
| eventOutcome | success | True | 47 | PASS |
| linkingAgentRole | executing program | True | 47 | PASS |
| linkingAgentRole | authorizer | True | 47 | PASS |
| linkingAgentRole | implementer | True | 0 | NOT PRESENT (scenario dependent) |
| linkingObjectRole | source | True | 15 | PASS |
| linkingObjectRole | outcome | True | 22 | PASS |
| agentType | software | True | 7 | PASS |
| agentType | person | True | 1 | PASS |
| agentType | organization | True | 0 | NOT PRESENT (scenario dependent) |

The saved example contains no manual implementer, organization agent, metadata revision or correction event. Implementer vocabulary support does not implement a manual QA/adjudication workflow. Historical committed PREMIS is never rewritten to add attributes. Findings and Workflow items require separate semantic and scenario checks. Legacy non-profile writer is outside this vocabulary audit.
