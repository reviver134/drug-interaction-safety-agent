# Drug Interaction Safety Agent

Upload prescription photos, review the extracted medications, confirm matched
ingredients, and check every unique ingredient pair. Gemini extracts names from
photos and explains returned DDInter/CYP graph evidence in simple language.
Manual entry works without Gemini. Missing evidence is reported explicitly;
it is never a safety determination. Conditions, QT, dosing, pharmacogenomics
and therapeutic alternatives are not assessed.

Photos can be previewed, removed and replaced. Changing a photo or medication
clears stale matches/results. Up to 5 JPEG/PNG/WebP photos, 8 MB each, are supported.
Files are validated and metadata is stripped before sending to Gemini; this app
does not persist uploaded photos or extracted prescription content. Google Gemini
processes submitted images under your configured account's data policies.

## Start locally (PowerShell)

Requires Python 3.11+, Node.js and a populated FalkorDB graph.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env: set GEMINI_API_KEY and optionally GEMINI_MODEL.
cd frontend
npm ci
npm run build
cd ..
.\venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 for the UI or `/docs` for the API. Development:
run `npm run dev` in `frontend`; Vite proxies `/api` to port 8000.
Set `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_GRAPH` in each process;
defaults: localhost, 6379, drug_interactions. Startup reuses the existing graph.
`.env` is loaded from the project root; process environment variables take precedence.
The API key stays on the server and is never sent to the browser.

The default model is `gemini-2.5-flash`; change GEMINI_MODEL to one available to
your account. Restart the API after changing configuration. Without a key, photo
reading is disabled and explanations use a deterministic graph-evidence template.
Gemini failures or invalid citations also fall back to that template.

## Data and graph loading

Bulk data and generated maps are in ignored `data/`. Move this folder outside
the repository and set `$env:DRUG_DATA_DIR = 'D:\drug-data'` in each process.
Keep `rrf/`, DDInter CSVs, `fda_table1_raw.csv` and generated name/ingredient maps
together. OpenFDA condition-warning records already loaded in the graph are shown separately from pair evidence. Pair checks include FDA enzyme and transporter paths. Raw OpenFDA and NDC archives are not read at runtime.
To rebuild OpenFDA product label context from the local RxNorm snapshot and label archives:

```powershell
.\venv\Scripts\python.exe rxnorm_ingredients.py
.\venv\Scripts\python.exe session4_openfda.py
```

The plural `data/rxcui_to_ingredients.json` preserves every base IN ingredient of
each mapped product. It follows directed RxNorm ingredient, component, precise
ingredient and MIN decomposition links; unresolved products are not guessed.
Session 1 also generates this map. The OpenFDA importer streams all local archives
and stores one representative label per exact ingredient set, choosing the latest
label date (record ID breaks ties). Descriptions, indications, contraindications,
boxed warnings, warnings and drug-interaction sections retain their source set ID,
record ID, date and DailyMed link. Sections longer than 8,000 characters are marked
as shortened. These are product-label excerpts; formulation-specific wording may
differ from the user's medicine. Combination labels require a complete ingredient
set match and are never assigned to a single component. Import counts and skipped
record reasons are saved in `data/openfda_import_report.json`. The older condition
excerpts remain available separately.

Gemini receives matched OpenFDA sections alongside pair evidence and can explain
medicine descriptions and label warnings even for a single medicine. Label
summaries and warnings cite individual source sections and stay separate from
pair interaction verdicts. Unsupported citations trigger source-excerpt fallback;
when Gemini is unavailable the original label excerpts remain visible.

Runtime matching needs the graph and `medication_aliases.json`, a deployable alias
index derived from the local RxNorm ingredient synonyms and IN/BN tradename links.
It preserves multi-ingredient brands instead of mapping them to one ingredient.
Run `python build_medication_aliases.py` after replacing RxNorm source data, and
restart the API/MCP processes. Regional generic synonym paracetamol resolves to
acetaminophen. Unknown/ambiguous names need correction, not approximate acceptance.
Fixture seeding additionally needs the name map in data/. Patient/scenario JSON
files remain for legacy demo endpoints; uploads never create patient records.

For a fresh graph:

```powershell
docker run -d --name drug-graph -p 6379:6379 -v drug-graph-data:/data falkordb/falkordb
.\venv\Scripts\python.exe session1_rxnorm.py
.\venv\Scripts\python.exe session2_ddinter.py
.\venv\Scripts\python.exe session3_fda_cyp.py
.\venv\Scripts\python.exe session5_patients.py
```

Loaders modify graph layers; back up an existing graph before rerunning them.
Configure database persistence and backups for deployment. Fresh loading and
container restart recovery were not exercised during interface consistency checks.
Loader logs are generated in data/logs/ and excluded from the deployment image.

## HTTP and MCP

The website uses:

- `POST /api/prescription/extract`: raw image bytes with image Content-Type.
  Returns structured names, strength/formulation, uncertainty and warnings.
- `POST /api/prescription/resolve`: `{"medications":["paracetamol","warfarin"]}`.
  Returns matched ingredients or explicit unresolved entries for user review.
- `POST /api/prescription/analyze`: same list plus `"confirmed":true`.
  Returns all unique resolved ingredient pairs, source paths, coverage gaps and
  a Gemini explanation (or labelled template fallback). Up to 20 medication
  entries / 30 expanded ingredients. Duplicate ingredients are checked once.
- `GET /api/config`: provider availability and upload constraints, without secrets.

Analysis is read only. Unresolved entries remain visible; they are not counted
as checked. Query failures return 503, never an empty successful result. Source
citations retain graph fields and record IDs; external links point to the source
dataset because the loaders do not supply per-record URLs. The backend always
adds missing-data notices independently of Gemini output.

Legacy patient endpoints remain available for existing API/MCP clients:

`POST /api/check` and `POST /prescriptions/check` accept
`{"patient_id":"PAT001","drug":"simvastatin"}` and return the canonical service
payload, including verdict, paths, explanation, decision_basis and query trace.
Unknown inputs return explicit UNKNOWN_PATIENT / UNKNOWN_DRUG values with HTTP 200,
matching MCP; malformed HTTP bodies produce validation errors.

Other routes: `GET /api/patients`, `GET /api/drugs?q=simva`,
`GET /patients/{id}/medications`, `POST /api/patients/{id}/meds` with `{"drug":"..."}`,
`DELETE /api/patients/{id}/meds/{drug}`, `POST /patients/seed-demo`.

Start MCP: `.\venv\Scripts\python.exe mcp_server.py`. Configure the client with
absolute executable/script paths and the same graph/data environment variables.
Tools: get_patient_context, find_interaction_paths, check_prescription,
change_patient_medication, seed_demo_patients, resolve_medications,
analyze_prescription. The latter requires confirmed=true and uses the same
read-only all-pairs service as the website.

Try PAT001/simvastatin, PAT002/fluconazole, PAT003/rifampin: BLOCK with Major DDInter
and CYP evidence. BLOCK requires review under demo rules, not an absolute prohibition.
Synthetic decision support only; not clinical advice.

## Verify

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe verify_project.py
.\venv\Scripts\python.exe -m unittest test_prescription_workflow -v
# Optional: calls the configured Gemini provider with a synthetic image.
.\venv\Scripts\python.exe verify_live_prescription.py
cd frontend
npm test
```

Python checks use the active graph read only and real MCP stdio to compare HTTP
and MCP pair results. Unit/integration checks cover aliases, combinations,
deduplication, provider failures, image validation, citations and missing evidence.
Gemini responses are controlled in tests; they do not prove live OCR accuracy.
Browser tests use headless Edge on Windows (Chromium elsewhere), with a temporary
API server on port 8765. Desktop/mobile checks cover image preview, removal,
replacement, extraction review and stale responses. Install Chromium with
`npx playwright install chromium` if needed on non-Windows systems.
The browser test server disables Gemini credentials for deterministic timing;
extraction is controlled and explanations use the graph template. The separate
live-provider test validates actual image extraction and Gemini explanations and
saves its synthetic-data report to verification/live_prescription_test.json.

## Deploy

`docker build -t drug-safety .` builds the UI and API image serving port 8000.
Provide graph environment variables when starting the container. The graph must
be reachable from it; localhost refers to the app container itself. Mount external
data at /app/data if loading or fixture seeding is needed. Data, environments,
logs and dependency folders are excluded from the image.
Deploy in a trusted environment: mutation endpoints do not implement authentication.
