# O*NET test fixture

A four-occupation software slice of real O\*NET 30.3 rows, for contract tests.

| | |
|---|---|
| Occupations | Software Developers (15-1252.00), Computer Programmers (15-1251.00), Database Architects (15-1243.00), Data Scientists (15-2051.00) |
| Descriptors | every rated element of all four Content Model branches — 52 abilities, 10 basic skills, 25 cross-functional skills, 33 knowledge areas |
| Also | 8 tasks and up to 12 lay job titles per occupation, plus the related-occupation edges that stay inside the slice |

Rows are copied **verbatim** from the source tables, which is what makes the
fixture trustworthy: `build_fixture.py` is a filter, not a transformation. It is
closed under its own edges (whole occupations, and only related-occupation edges
whose both ends are in the slice) so the loader's endpoint validation has
something real to check.

Regenerate after an O\*NET release bump:

```bash
python -m ta_taxonomies.suites.onet.fixtures.build_fixture \
    --data-dir data/onet/raw/db_30_3_text
```

**Attribution:** includes information from the [O\*NET 30.3 Database](https://www.onetcenter.org/database.html)
by USDOL/ETA, used under [CC BY 4.0](https://www.onetcenter.org/license_db.html).
O\*NET® is a trademark of USDOL/ETA. Redistribution is permitted with
attribution, which is why these rows may be committed where a licence-restricted
source's could not.
