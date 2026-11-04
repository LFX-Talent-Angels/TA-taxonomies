"""JobTech suite package: Sweden's labour-market taxonomy (Arbetsförmedlingen, CC0).

Occupation names under SSYK 2012, skills under skill headlines, the skills an
SSYK unit group asks for, substitutability between occupations, and JobTech's
own matches to ESCO kept as URIs. Labels are Swedish. ``fetch`` builds one
JSON document from the pinned bulk files and the GraphQL API, ``load`` puts it
in Neo4j, and the suite tools query it. See NOTES.md.
"""
