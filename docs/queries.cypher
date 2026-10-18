// Select graph drug_interactions in FalkorDB Browser.
// Run each statement separately. These queries do not write data.

// 1. Verify that the graph has drug data.
MATCH (d:Drug)
RETURN count(d) AS drugs;

// 2. Resolve two exact generic names.
MATCH (d:Drug)
WHERE d.name_lower IN ['simvastatin', 'fluconazole']
RETURN d.rxcui, d.name, d.name_lower;

// 3. Follow a direct DDInter relationship and retain original source IDs.
MATCH (a:Drug {name_lower: 'simvastatin'})-[r:INTERACTS_WITH]-(b:Drug {name_lower: 'fluconazole'})
RETURN a.name, b.name, r.severity, r.ddinter_id_a, r.ddinter_id_b,
       coalesce(r.source, 'DDInter') AS source;

// 4. Multi-hop mechanism: a drug inhibits/induces a shared target of another drug.
MATCH (a:Drug {name_lower: 'fluconazole'})-[effect:INHIBITS|INDUCES]->(target)
      <-[substrate:SUBSTRATE_OF]-(b:Drug {name_lower: 'simvastatin'})
WHERE target:Enzyme OR target:Transporter
RETURN a.name, type(effect), effect.strength, target.symbol,
       b.name, substrate.strength,
       coalesce(effect.source, 'FDA CYP/transporter table') AS source;

// 5. Exact ingredient-set label context for simvastatin alone.
// Combination labels have a different key and are deliberately excluded.
MATCH (d:Drug {name_lower: 'simvastatin'})-[:HAS_LABEL]->(l:OpenFDALabel)
WHERE l.key = d.rxcui
RETURN d.name, l.set_id, l.record_id, l.effective_time, l.source_url;
