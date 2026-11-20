# O*NET suite

US Department of Labor Occupational Information Network.

This package is a skeleton until the loader ships. The **measured freeze** for
v1 (which files, keys, and counts) lives in [`INVENTORY.md`](./INVENTORY.md).
Do not start from the Sprint 1 30.3 notes — pin **31.0**.

## Source & license

- **Release:** O*NET 31.0 (August 2026)
- **Download (text/TSV):** https://www.onetcenter.org/dl_files/database/db_31_0_text.zip
- **Database page:** https://www.onetcenter.org/database.html
- **Dictionary:** https://www.onetcenter.org/dictionary/31.0/text/
- **License:** [CC BY 4.0](https://www.onetcenter.org/license_db.html)

**Attribution** (verbatim form; include the version number):

> This page includes information from the O*NET 31.0 Database by the U.S.
> Department of Labor, Employment and Training Administration (USDOL/ETA).
> Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA.

Use "O*NET" as an adjective ("O*NET data") — never possessive or plural.

## Local dump (gitignored)

Full TSVs are **never committed**. They live under `data/onet/raw/` (the repo
`.gitignore` ignores `/data/`).

```bash
cd TA-taxonomies
mkdir -p data/onet/raw
curl -L -o data/onet/db_31_0_text.zip \
  "https://www.onetcenter.org/dl_files/database/db_31_0_text.zip"
shasum -a 256 data/onet/db_31_0_text.zip
# expected: 6883548adf5fde64cf6f801b35d15519c9225f2732c3cab0e281c652d16b23a9
unzip -d data/onet data/onet/db_31_0_text.zip
mv data/onet/db_31_0_text/* data/onet/raw/
rmdir data/onet/db_31_0_text
export ONET_DATA_DIR=data/onet/raw
ls "$ONET_DATA_DIR/Occupation Data.txt"
```

The website "All Files" button currently points at the Excel zip
(`db_31_0_excel.zip`). This suite uses the **text** zip, which is still
published at the URL above (same files as Sprint 1, tab-delimited).

## Load

`--mode full` is the product graph: every taxonomy-knowledge file in 31.0
(see [`INVENTORY.md`](./INVENTORY.md)). `--mode fixture` is a small committed
slice of that **same** schema for tests. Do not run fixture wipe against a
demo database that holds a full O*NET (or ESCO) graph.

```bash
python -m ta_taxonomies.suites.onet.load --mode full     # product
python -m ta_taxonomies.suites.onet.load --mode fixture  # CI only
```

Same Neo4j as ESCO (`ta-neo4j`, `bolt://localhost:7687`). O*NET nodes carry
`:OnetNode` and `source="onet"`; wipe is suite-scoped.

Browser cheat-sheet: [`queries.cypher`](./queries.cypher). Load/repro:
[`NOTES.md`](./NOTES.md).
