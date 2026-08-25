# SFIA suite

The **Skills Framework for the Information Age**, version 9: 147 professional
skills and seven levels of responsibility.

## Read this first — the licence decides the design

SFIA's free licence covers **personal and internal use**. A fee-bearing licence
is required for *"redistributing SFIA material in electronic or printed form to
any other organisation"*
([licensing](https://sfia-online.org/en/about-sfia/licensing-sfia)), and a public
Apache-2.0 repository is redistribution to everyone. TA-workspace **ADR-0006 §2**
therefore adopts SFIA **for structure only**.

| Stored here | Never stored here |
|---|---|
| Skill codes (`PROG`, `ISCO`) | The skill's description |
| Skill names | The "essence of the level" |
| Level numbers 1–7 | The definition of a skill at a level |
| Which levels a skill is defined at | Guidance notes |
| Published related-skill links | Any other SFIA prose |
| Category / subcategory, when the source carries them | |

Every skill node carries `source_url`, the address of the definition this
repository deliberately does not copy. That is what "pointer, not payload" means
here: a caller holding their own SFIA access can fetch the text at runtime.

The rule is enforced rather than intended — see `extract.py` and
`tests/suites/sfia/test_licensing.py`. Adding a licensed field requires editing
`ALLOWED_NODE_KEYS` in `config.py`, which is a visible, reviewable act.

## What this suite is for

SFIA has **no occupations**, so it cannot answer "what skills does job X need".
What it has and no other adopted source does is a **responsibility axis**: seven
ordered levels, and the published fact of which of them each skill is defined at.
See NOTES.md for the questions that unlocks.

## Graph model

```text
(:SfiaSkill)-[:HAS_LEVEL {level}]->(:SfiaLevel)
(:SfiaLevel)-[:MAY_LEAD_TO {from_level, to_level}]->(:SfiaLevel)
(:SfiaSkill)-[:RELATED_TO]->(:SfiaSkill)
(:SfiaSkill|:SfiaSubcategory)-[:BROADER_THAN]->(:SfiaSubcategory|:SfiaCategory)
```

Every node carries three labels: `:SfiaNode` (umbrella, constrained unique),
a suite-scoped concrete label, and the canonical `ARCHITECTURE.md` one.

## Getting the data

SFIA publishes **no public bulk download** — the spreadsheet distribution is
behind registration — so `--mode full` reads a local snapshot you fetch under
your own licence. It is gitignored and must never be committed.

```bash
# A throwaway Neo4j. A load DELETES this suite's nodes, so never point it at a
# graph you care about.
docker run -d --name ta-neo4j-sfia -p 7482:7474 -p 7695:7687 \
    -e NEO4J_AUTH=neo4j/sfia-dev neo4j:5-community

export SFIA_NEO4J_URI=bolt://localhost:7695
export SFIA_NEO4J_PASSWORD=sfia-dev

python -m ta_taxonomies.suites.sfia.fetch --data-dir data/sfia/raw
python -m ta_taxonomies.suites.sfia.load --mode fixture
python -m ta_taxonomies.suites.sfia.load --mode full --data-dir data/sfia/raw

pytest tests/suites/sfia -q
```

### Or: your own export from the SFIA spreadsheet

If you hold SFIA's spreadsheet distribution, export a `structure.csv` into the
same directory and the loader prefers it. This is the only path that carries the
**category tree**, which the public pages do not expose.

| Column | Meaning |
|---|---|
| `skill_code` | four letters, e.g. `PROG` |
| `skill_name` | the skill's name |
| `category` | optional |
| `subcategory` | optional |
| `levels` | semicolon-separated level numbers, e.g. `2;3;4;5;6` |

Any other column in the file is ignored rather than loaded — including a
description column you forgot to delete.

## Attribution

SFIA® is a registered trademark of the SFIA Foundation. This suite stores no
SFIA material beyond the factual identifiers described above and is not endorsed
by, affiliated with, or a licensed product of the SFIA Foundation. Anyone using
SFIA needs their own licence: <https://sfia-online.org/en/about-sfia/licensing-sfia>.
