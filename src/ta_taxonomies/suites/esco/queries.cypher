// ESCO suite — smoke / demo queries (Neo4j Browser)
// Connect: bolt://localhost:7687  user: neo4j  password: taxonomies-dev  (local Docker)

// Filter :EscoNode / source:'esco' — Occupation is shared with O*NET in one DB.

// --- Inventory ---
MATCH (n:EscoNode:Occupation) RETURN count(n) AS occupations;
MATCH (n:EscoNode:Skill) RETURN count(n) AS skills;
MATCH (n:ISCOGroup) RETURN count(n) AS isco_groups;
MATCH (n:SkillGroup) RETURN count(n) AS skill_groups;
MATCH (:EscoNode)-[r:HAS_SKILL]->() RETURN count(r) AS has_skill;
MATCH (:EscoNode)-[r:BROADER_THAN]->() RETURN count(r) AS broader_than;
MATCH (:EscoNode)-[r:CLASSIFIED_UNDER]->() RETURN count(r) AS classified_under;
MATCH (:EscoNode)-[r:RELATED_TO]->() RETURN count(r) AS related_to;

// --- Locate: exact occupation ---
MATCH (o:EscoNode:Occupation)
WHERE toLower(o.pref_label) = 'software developer'
RETURN o.id, o.pref_label, o.code, o.uri;

// --- Connect: essential + optional skills ---
MATCH (o:EscoNode:Occupation {pref_label: 'software developer'})-[r:HAS_SKILL]->(s:Skill)
RETURN r.relation_type AS rel, s.pref_label AS skill, s.skill_type AS skill_type
ORDER BY rel, skill
LIMIT 50;

// --- Hierarchy: occupation → ISCO ---
MATCH (o:EscoNode:Occupation {pref_label: 'software developer'})-[:CLASSIFIED_UNDER]->(g:ISCOGroup)
RETURN o.pref_label, g.code, g.pref_label, g.id;

// --- Broader chain (one hop up from occupation) ---
MATCH (o:EscoNode:Occupation {pref_label: 'software developer'})-[:BROADER_THAN]->(b)
RETURN labels(b) AS labels, b.pref_label, b.id;

// --- Skill-gap sketch: essential skills in data scientist not in data analyst ---
MATCH (target:EscoNode:Occupation {pref_label: 'data scientist'})-[:HAS_SKILL {relation_type: 'essential'}]->(s:Skill)
WHERE NOT EXISTS {
  MATCH (source:EscoNode:Occupation {pref_label: 'data analyst'})-[:HAS_SKILL {relation_type: 'essential'}]->(s)
}
RETURN s.pref_label AS missing_essential_skill
ORDER BY missing_essential_skill
LIMIT 40;

// --- Occupations with no skills (should be 0 after full ESCO load) ---
MATCH (o:EscoNode:Occupation)
WHERE NOT (o)-[:HAS_SKILL]->()
RETURN count(o) AS occupations_without_skills;
