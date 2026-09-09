// O*NET suite — smoke / demo queries (Neo4j Browser)
// Connect: bolt://localhost:7687  user: neo4j  password: taxonomies-dev
// Always filter :OnetNode or source:'onet'. Occupation is shared with ESCO.

// --- Split inventory (both suites in one database) ---
MATCH (n) WHERE n.source IS NOT NULL
RETURN n.source AS suite, count(n) AS nodes
ORDER BY suite;

MATCH (n:OnetNode) RETURN count(n) AS onet_nodes;
MATCH (n:EscoNode) RETURN count(n) AS esco_nodes;

MATCH (n:OnetNode:Occupation) RETURN count(n) AS onet_occupations;
MATCH (n:OnetNode)-[r:HAS_SKILL]->() RETURN count(r) AS onet_has_skill;
MATCH (n:OnetNode)-[r:USES_SOFTWARE]->() RETURN count(r) AS onet_uses_software;

// --- No bridge between suites (must be 0 until crosswalks exist) ---
MATCH (a)-[r]->(b)
WHERE a.source IS NOT NULL AND b.source IS NOT NULL AND a.source <> b.source
RETURN count(r) AS cross_suite_edges;

// --- Locate: exact preferred title ---
MATCH (o:OnetNode:Occupation {pref_label: 'Software Developers'})
RETURN o.id, o.pref_label, o.code, o.source;

// --- Locate: job-title alias ---
MATCH (o:OnetNode:Occupation)
WHERE 'Software Engineer' IN coalesce(o.alt_labels, [])
RETURN o.id, o.pref_label, o.code;

// --- Connect: weighted skills ---
MATCH (o:OnetNode {id: 'onet:occupation:15-1252.00'})-[r:HAS_SKILL]->(s)
RETURN r.relation_type AS rel, s.pref_label AS skill, r.importance AS importance, r.level AS level
ORDER BY coalesce(r.importance, 0) DESC, skill
LIMIT 25;

// --- Connect: software / tools ---
MATCH (o:OnetNode {id: 'onet:occupation:15-1252.00'})-[r:USES_SOFTWARE]->(sw:Software)
RETURN sw.pref_label AS tool, r.hot_technology AS hot, r.in_demand AS in_demand
ORDER BY tool
LIMIT 25;

// --- Related occupations ---
MATCH (o:OnetNode {id: 'onet:occupation:15-1252.00'})-[r:RELATED_TO]->(other:OnetNode)
RETURN other.pref_label, other.id, r.relatedness_tier
ORDER BY r.index
LIMIT 20;
