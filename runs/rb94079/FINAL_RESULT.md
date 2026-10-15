# Live workflow result: 2026_012405

DmsJobId **12405.26**; symbol **G/TBT/N/EU/1243**.

**COMPLETED ? isolated live example, not production accession.**

- Received originals: **8** (3 Word files, 3 published DOL PDFs, 2 notification PDFs).
- Preservation masters: **5**; veraPDF PDF/A-2b: **5 PASS**; automated fidelity QA: **5 CLEAR**.
- SIP payload: **37 files**, **2133665.37** Payload-Oxum.
- OCFL: **v1**; independent validator: **PASS**.
- All staged original SHA-512 values verified; pre-existing production files unchanged.

| File | Language | Pages | PDF/A-2b | QA |
| --- | --- | ---: | --- | --- |
| 26_05201_00_e.pdf | en | 3 | PASS | CLEAR |
| 26_05201_01_e.pdf | en | 2 | PASS | CLEAR |
| EU1243.pdf | en | 2 | PASS | CLEAR |
| EU1243.pdf | es | 2 | PASS | CLEAR |
| EU1243.pdf | fr | 2 | PASS | CLEAR |

The authorized unsigned-derivative policy was applied to both notification PDFs. Their originals remain unchanged and are retained in the SIP/AIP. The derived masters make no claim to retain the originals' cryptographic signatures. PREMIS records the policy on normalization events.

## Results to inspect

- [SIP bag](staging/2026_012405/bag-info.txt)
- [OCFL inventory](AIP/2026/012000/2026_012405/inventory.json)
- [AIP PREMIS](AIP/2026/012000/2026_012405/v1/content/metadata/preservation/2026_012405_premis.xml)
- [AIP METS](AIP/2026/012000/2026_012405/v1/content/2026_012405_mets.xml)
- [DOME](AIP/2026/012000/2026_012405/v1/content/metadata/descriptive/2026_012405_dome.xml)
- [Machine-readable summary](final_summary.json)
- [All stage attempts and logs](run_result.json)

## Draft-profile limitations

The registry/crosswalk remain provisional. DROID did not assign PRONOM identifiers to the five native Aspose XML conversion logs. Those logs are retained unchanged; metadata records the identification gaps rather than inventing PUIDs. XML/XSD and package fixity checks passed. This is not a declaration of full institutional profile approval.

DROID identifies the two attachment masters generically as PDF 1.7 (fmt/276),
while veraPDF independently validates both as PDF/A-2b. Both observations are
retained; no stronger PRONOM identification has been invented.

## Downloadable packages

- [SIP ZIP](2026_012405_SIP.zip)
- [OCFL v1 AIP ZIP](2026_012405_AIP_OCFL_v1.zip)

The SIP retains the three Word originals and two notification originals plus
five preservation masters. The three DOL published `_src.pdf` working copies
remain in preparation and are excluded from the bag under the existing DOL
policy; PREMIS retains their identifiers, fixity and derivation relationships.
