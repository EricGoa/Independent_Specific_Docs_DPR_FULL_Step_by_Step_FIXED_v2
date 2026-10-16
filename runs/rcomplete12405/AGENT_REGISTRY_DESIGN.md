# Agent and UUID registry design v1
Status: user-authorized working design, 2026-10-16.

Agent rows are explicit configuration in preservation_config.json or --agent-registry.
Fields: uuid (lowercase UUID4), name, type, version for software, note and email
for the operator. Distinct software versions have distinct UUIDs. Source-set
SHA-256 fingerprints identify actual script code until release tags exist;
they must not be represented as production release tags. Measured tool versions,
DROID signature versions and the actual .NET Aspose edition are recorded.
Separate packaging and verification agents prevent misattribution to PoC6/PoC9.
The live example's operator is Eric Gao, eric.gao@wto.org, as supplied in this
conversation, with explicitly provisional institutional status. No staff identities
are fabricated. Default automated tests use a clearly synthetic operator.

Every newly assigned UUID is registered before use. The shared registry under
the run root is .registries/uuid-registry.json for lookup and the append-only
uuid-registry.csv audit ledger, columns uuid, entity_type, job_id, symbol,
description, minted_at, minted_by. Existing UUIDs are reused only for the same
persistent entity. Collisions, changed existing rows or lost entries hold the job.
The CSV is append-only; it is not rewritten to change old facts.
The local implementation requires one workflow writer per registry root.
Parallel production writers require a transactional SQL registry; this design
does not claim multi-host locking. Choose one repository/run root for a job
and share it among every stage. Independent isolated examples have their own roots.

PREMIS copies configuration records and links agents by UUID. Every event has
executing program and authorizer roles; additional software versions remain
separate agents. Agents first introduced in this version are ordered by their
first event reference, while historical agent elements remain unchanged.
Manual reviewers must be real supplied registry records. No human implementer
is emitted for an automated-only QA run.
