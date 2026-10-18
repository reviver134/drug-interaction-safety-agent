# Graph model and execution

## Track and architecture

This project targets Graph Hacks Track 01: Agents That Act on Connected Data.
The React interface calls FastAPI; FastAPI resolves medication names and queries
FalkorDB before applying deterministic review rules. Gemini handles optional
image extraction and source-grounded explanation. The graph determines the
evidence and verdict; the language model cannot override them.

The website runs a fixed, human-confirmed workflow. The separate MCP stdio server
exposes tools to agent clients; the website does not itself choose MCP tools or
implement autonomous planning. No persistent agent memory or agent coordination
is claimed. The daily availability query is operational automation.

## Nodes

| Label | Identity / important properties | Purpose |
|---|---|---|
| `Drug` | `rxcui`, `name`, `name_lower`, `aliases`, `unii` | Canonical ingredient/drug matching |
| `Enzyme` | `symbol` | Shared metabolic mechanisms |
| `Transporter` | `symbol` | Shared transport mechanisms |
| `Condition` | `name`, `name_lower` | Legacy drug-level label warnings |
| `OpenFDALabel` | `key`, `ingredient_ids`, `set_id`, `record_id`, `effective_time`, `source_url`, `sections_json` | Label sections keyed to an exact ingredient set |
| `Patient` | `id`, `name`, `age` | Synthetic legacy demo context |
| `Prescription` | `id`, `drug_rxcui`, `drug_name`, `dose`, `frequency`, `start_date` | Synthetic medication context |

`Drug.rxcui`, `Drug.name_lower`, `Drug.unii` and `Condition.name_lower` have
lookup indexes. Name/ingredient matching also uses the bundled exact-name alias
index; each accepted ingredient must exist in FalkorDB.

## Relationships

| Pattern | Meaning / evidence |
|---|---|
| `(Drug)-[:INTERACTS_WITH]-(Drug)` | DDInter pair, including severity and original DDInter IDs; queried in either direction |
| `(Drug)-[:INHIBITS]->(Enzyme or Transporter)` | Inhibition with source and strength |
| `(Drug)-[:INDUCES]->(Enzyme or Transporter)` | Induction with source and strength |
| `(Drug)-[:SUBSTRATE_OF]->(Enzyme or Transporter)` | A shared target that can connect an interacting pair |
| `(Drug)-[:CONTRAINDICATED_IN]->(Condition)` | Drug-level label excerpt; not pair-interaction evidence |
| `(Drug)-[:HAS_LABEL]->(OpenFDALabel)` | Ingredient membership in an exact ingredient-set label |
| `(Patient)-[:TAKES]->(Prescription)-[:OF_DRUG]->(Drug)` | Synthetic patient's current medicines |

A combination label may have several incoming `HAS_LABEL` edges. The application
matches its complete sorted `ingredient_ids` key before displaying its sections;
one ingredient's membership is not enough to assign the combination's label.

## How a request uses the graph

1. Gemini extracts candidate names from an uploaded image, or the user types them.
2. Exact generic/alias matching resolves ingredient identities in the graph.
   Unknown or ambiguous entries remain unresolved for user correction.
3. The user confirms the resolved medicines. The service expands combination
   brands and deduplicates ingredients by RXCUI.
4. For every unique ingredient pair, Cypher checks the direct DDInter edge and
   both orientations of the two-hop inhibition/induction versus substrate path.
5. Deterministic demo rules produce review statuses from those returned paths.
   Missing evidence is reported explicitly and does not establish safety.
6. Exact-set OpenFDA labels provide separate medicine descriptions and warnings.
   Gemini receives the returned evidence and citation IDs for explanation.
   Invalid citations or provider failures trigger a labeled fallback.

The UI retains evidence paths, severity and sources for review. Legacy patient
checks also return the executed query trace. Database errors return 503 rather
than an empty successful result. Public patient mutation HTTP routes are disabled.

## Queries and implementation

Run [queries.cypher](queries.cypher) in the FalkorDB Browser against
`drug_interactions`. These examples are read-only and do not require patient data.

The full parameterized queries and result interpretation are in
[`prescription_service.py`](../prescription_service.py), particularly
`PATIENT_CONTEXT_QUERY` and `PRESCRIPTION_PATHS_QUERY`.
[`prescription_analysis.py`](../prescription_analysis.py) constructs `PAIR_QUERY`
from that same implementation for prescription uploads and manual input.

`mcp_server.py` exposes `resolve_medications`, `analyze_prescription`,
`get_patient_context`, `find_interaction_paths`, `check_prescription`,
`change_patient_medication` and `seed_demo_patients`. Mutation tools operate on
legacy patient records and should be used only with a trusted synthetic graph.

## Evaluation and limits

The migrated demo graph was verified to contain 9,083 nodes, 117,223 relationships
and 6,834 Drug nodes on October 18, 2026. These are snapshot counts, not promises
of current coverage or clinical completeness. Twelve prescription workflow tests
passed against that migrated graph. Live deployment checks verified UI assets,
database health, ingredient resolution and a pair analysis.

The system is a hackathon demonstration, not a validated clinical decision system.
It does not assess dose, formulation-specific applicability, individual conditions,
QT risk, pharmacogenomics or therapeutic alternatives. FalkorDB Cloud Free lacks
persistence; retain source data and a local export for recovery.
