# O*NET test fixture

Small **ICT-oriented** subset of real O*NET 31.0 rows for contract tests
(~345 rows / 99 KB — same order of magnitude as the ESCO fixture).

- Occupations: Software Developers (`15-1252.00`), Computer Programmers,
  Software QA Analysts and Testers, Registered Nurses
- Same raw-table schema as `--mode full` (titles, tasks, weighted skills/
  knowledge/abilities, software examples, related occupations, …) but
  trimmed to 2 rows per element per occupation
- **Not** a full dump — full TSVs stay under gitignored `data/onet/raw/`

**Attribution:** O*NET 31.0 Database by USDOL/ETA, CC BY 4.0.
