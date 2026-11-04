# JobTech test fixture

A subset of the real JobTech document (version 31, `data/jobtech/jobtech_document.json`,
built by `python -m ta_taxonomies.suites.jobtech.fetch`), in the same shape. CC0 data,
committed as published.

- **11 occupations:**
  - Bagare/Konditor (with aliases "Bagare", "Konditor"; exact ESCO matches)
  - Kock à la carte
  - Restaurangchef (*chef* = manager: the false-friend case)
  - Barnmorska (direct essential skills)
  - Sjuksköterska, grundutbildad
  - Mjukvaruutvecklare
  - Systemutvecklare/Programmerare
  - Elevassistent ↔ Skolvärd (substitutability 75% both ways)
  - Assistent i församlingsarbete (SSYK 3)
  - AD-assistent (SSYK 4)
- **Their SSYK groups** (31, levels 1–4).
- **80 skills:** every direct skill of these occupations, plus the first six of each unit group's skills. Also their 12 headlines.

What the conformance test uses:
- exact title "Barnmorska"
- broad query "utvecklare"
- group query "assistent" (SSYK majors 3 and 4)
- code `2222` (the Barnmorska unit group)
