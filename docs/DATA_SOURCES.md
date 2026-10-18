# Data sources and rebuilding the graph

## Provenance and access

| Source | Used for | Where to obtain it |
|---|---|---|
| NLM RxNorm | Ingredient names, RXCUIs, aliases and product-to-ingredient links | [Official release files](https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html) |
| DDInter | Drug-pair severity and DDInter record IDs | [Public CSV downloads](https://ddinter.scbdd.com/download/) |
| FDA CYP/transporter table | Enzyme/transporter effects and substrate relationships | [FDA Table 1](https://www.fda.gov/drugs/drug-interactions-labeling/healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems) |
| openFDA drug labels | Source-attributed descriptions, warnings and interactions sections | [Official bulk downloads](https://open.fda.gov/data/downloads/) — Human Drug Label |
| Repository patient/scenario fixtures | Synthetic legacy demo context | `demo_patients.json`, `demo_scenarios.json` |

Third-party data retains its source terms; this repository does not grant rights
to those datasets. DDInter's download page links its terms and conditions.
Source IDs and label links are retained for attribution. Uploaded prescription
images and extracted content are not persisted as graph records by this app.

NLM distinguishes the full RxNorm release (license/account required) from Current
Prescribable Content (listed as requiring no license). A publicly downloadable
subset is the appropriate starting point for a reproduction that must avoid
login-protected data. Its coverage can differ from a full local snapshot.

The original local RxNorm archive's acquisition history has not been independently
verified. Its loader can consume aliases from multiple source vocabularies, so
the deployed graph is not claimed to have been rebuilt exclusively from the
public subset. Confirm source provenance and the event's data-use requirements
before attesting eligibility. Changing this document does not change the existing
graph or its data rights.

## Input layout

Put the following under `data/`, or set `DRUG_DATA_DIR` to an absolute directory:

```text
data/
  rrf/
    RXNCONSO.RRF
    RXNREL.RRF
    RXNSAT.RRF
  ddinter_downloads_code_A.csv
  ddinter_downloads_code_B.csv
  ddinter_downloads_code_D.csv
  ddinter_downloads_code_H.csv
  ddinter_downloads_code_L.csv
  ddinter_downloads_code_P.csv
  ddinter_downloads_code_R.csv
  fda_table1_raw.csv
  openfda_labels/
    drug-label-*.json.zip
```

Extract the RRF files from the chosen RxNorm release. Download the seven DDInter
CSV categories listed above, keeping their names and columns (`Drug_A`, `Drug_B`,
`DDInterID_A`, `DDInterID_B`, `Level`). Save FDA Table 1 as UTF-8 CSV, preserving
its original column headings, including `Drug or Other Substance`. Download the
openFDA Human Drug Label ZIP partitions without unzipping them into the source
directory; the importer streams their JSON members. `RXNSAT.RRF` is optional for
the core graph but provides ATC attributes when available in the chosen release.
The NDC archive is not required by the runtime or the label importer.

## Loader order

Create a virtual environment and install `requirements.txt` as in the root
README. Copy `.env.example` to `.env` and point it at a new local or cloud graph.
Back up existing data before rerunning loaders: some steps replace graph layers.
Do not run this sequence against the hosted demo just to test connectivity.

```powershell
.\venv\Scripts\python.exe session1_rxnorm.py
.\venv\Scripts\python.exe session2_ddinter.py
.\venv\Scripts\python.exe session3_fda_cyp.py
.\venv\Scripts\python.exe rxnorm_ingredients.py
.\venv\Scripts\python.exe session4_openfda.py
.\venv\Scripts\python.exe build_medication_aliases.py
# Optional synthetic patients for legacy HTTP/MCP tools:
.\venv\Scripts\python.exe session5_patients.py
```

Session 1 generates `name_to_rxcui.json` and the singular legacy ingredient map.
`rxnorm_ingredients.py` generates the plural `rxcui_to_ingredients.json` required
by the OpenFDA importer. `build_medication_aliases.py` writes the deployable
root-level alias index; restart API/MCP processes after rebuilding it.
Loader logs and import reports are written under the ignored data directory.

The current OpenFDA loader creates `OpenFDALabel` nodes and `HAS_LABEL` edges.
It does not recreate older `CONTRAINDICATED_IN` condition-warning edges; the app
shows those separately if present in an existing graph. Rebuilds can therefore
differ from the migrated snapshot's legacy condition layer.

## Data checks

Run the read-only examples in [queries.cypher](queries.cypher), then use the root
README verification commands. Source releases change, so counts can differ from
the deployed snapshot. Missing labels or interactions mean missing coverage;
they do not establish safety. Test images and patient fixtures must be synthetic
and contain no real personal information.
