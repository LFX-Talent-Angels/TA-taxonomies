// SFIA suite — smoke / demo queries (Neo4j Browser)
// Connect: bolt://localhost:7687  user: neo4j  password: taxonomies-dev  (local Docker)

// Filter :SfiaNode / source:'sfia' — Skill is shared with ESCO and O*NET in one DB.
// Structure only: no SFIA prose is stored; follow s.url for the licensed text.

// --- Inventory ---
MATCH (n:SfiaNode:Category) RETURN count(n) AS categories;
MATCH (n:SfiaNode:Subcategory) RETURN count(n) AS subcategories;
MATCH (n:SfiaNode:Skill) RETURN count(n) AS skills;
MATCH (n:SfiaNode:SkillLevel) RETURN count(n) AS skill_levels;
MATCH (:SfiaNode)-[r:HAS_LEVEL]->() RETURN count(r) AS has_level;
MATCH (:SfiaNode)-[r:BROADER_THAN]->() RETURN count(r) AS broader_than;

// --- Locate: a skill by code (SFIA "ISCO" is a skill, not the ISCO classification) ---
MATCH (s:SfiaNode:Skill {code: 'ISCO'})
RETURN s.id, s.pref_label, s.sfia_ref, s.url;

// --- Skills defined at level 6 ---
MATCH (s:SfiaNode:Skill)-[:HAS_LEVEL]->(l:SkillLevel {level: 6})
RETURN s.code AS code, s.pref_label AS skill, l.sfia_ref AS ref
ORDER BY code;

// --- PROG's level progression ---
MATCH (s:SfiaNode:Skill {code: 'PROG'})-[:HAS_LEVEL]->(l:SkillLevel)
RETURN l.level AS level, l.code AS code, l.sfia_ref AS ref
ORDER BY level;

// --- Category tree: category → subcategory → skills ---
MATCH (s:SfiaNode:Skill)-[:BROADER_THAN]->(sub:Subcategory)-[:BROADER_THAN]->(cat:Category)
RETURN cat.pref_label AS category, sub.pref_label AS subcategory,
       collect(s.code) AS skills
ORDER BY category, subcategory;

// --- Skills without levels (should be 0 after any load) ---
MATCH (s:SfiaNode:Skill)
WHERE NOT (s)-[:HAS_LEVEL]->()
RETURN count(s) AS skills_without_levels;
