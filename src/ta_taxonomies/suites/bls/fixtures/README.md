# BLS test fixture

A subset of the real BLS document (`data/bls/bls_document.json`, built by
`python -m ta_taxonomies.suites.bls.fetch`), in the same shape, so one loader
serves both. BLS data is US public domain, so the real text is committed.

- **15 occupations:**
  - computing: 15-1251 Computer Programmers, 15-1252 Software Developers,
    15-1253 Software QA Analysts and Testers, 15-1254 Web Developers,
    15-1255 Web and Digital Interface Designers, 15-2051 Data Scientists
  - health: 29-1071 Physician Assistants, 29-1141 Registered Nurses,
    29-1161 Nurse Midwives, 29-1171 Nurse Practitioners, 31-1121 Home Health
    Aides, 31-1131 Nursing Assistants, 31-9092 Medical Assistants
  - other: 51-3011 Bakers; 11-1031 Legislators (no EP data, so it tests the
    missing-projection path)
- **31-1121 Home Health Aides** carries EP's aggregate row `31-1120`
  (`projection_aggregate: true`).
- **The 23 SOC groups** above them (major, minor and broad), and **all 17 EP skills**.

What the conformance test uses:
- exact title "Software Developers"
- broad query "developers" (Software Developers, Web Developers)
- group query "assistants": Physician Assistants (SOC 29), Nursing and Medical
  Assistants (SOC 31)
- code `15-1252`

To rebuild it after a new release, select the same codes from the new
document, with every ancestor group of each code and every skill.
