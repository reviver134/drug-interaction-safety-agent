"""HTTP interface for the shared prescription service and React application."""
from pathlib import Path
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import prescription_service as service
import prescription_analysis as analysis
import gemini_service
from session5_patients import seed_demo_patients

app = FastAPI(title="Drug Interaction Safety API")


@app.middleware('http')
async def restrict_patient_mutations(request: Request, call_next):
    if os.environ.get('ENABLE_PATIENT_MUTATIONS', 'true').lower() != 'true':
        if (request.method in {'POST', 'DELETE'} and
                (request.url.path.startswith('/api/patients/') or
                 request.url.path.rstrip('/') == '/patients/seed-demo')):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={'detail': 'Patient changes are disabled.'})
    return await call_next(request)


class CheckRequest(BaseModel):
    patient_id: str = Field(min_length=1)
    drug: str = Field(min_length=1)


class MedicationRequest(BaseModel):
    drug: str = Field(min_length=1)


class PrescriptionList(BaseModel):
    medications: list[str] = Field(min_length=1, max_length=20)


class ConfirmedPrescription(PrescriptionList):
    confirmed: bool


def clean_names(request):
    names = [name.strip() for name in request.medications]
    if any(not name or len(name) > 160 for name in names):
        raise HTTPException(422, 'Enter medication names between 1 and 160 characters.')
    return names


@app.get('/api/config')
def configuration():
    return {'gemini_configured': gemini_service.configured(), 'max_image_bytes': 8 * 1024 * 1024,
            'image_types': ['image/jpeg', 'image/png', 'image/webp']}


@app.post('/api/prescription/extract')
async def extract(request: Request):
    if request.headers.get('content-type', '').split(';')[0] not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise HTTPException(415, 'Upload a JPEG, PNG or WebP prescription photo.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(413, 'Each prescription photo must be 8 MB or smaller.')
    try:
        return await run_in_threadpool(gemini_service.extract_medications, bytes(raw))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except gemini_service.GeminiUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post('/api/prescription/resolve')
def resolve_list(request: PrescriptionList):
    names = clean_names(request)
    try:
        return {'medications': analysis.resolve_list(service.connect_graph(), names)}
    except Exception as exc:
        raise HTTPException(503, 'Medication matching is unavailable. Check the graph connection and try again.') from exc


@app.post('/api/prescription/analyze')
def analyze(request: ConfirmedPrescription):
    if not request.confirmed:
        raise HTTPException(422, 'Review and confirm medication names before checking interactions.')
    names = clean_names(request)
    try:
        result = analysis.analyze_medications(service.connect_graph(), names)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # A failed graph query must not be represented as absence of interaction data.
        raise HTTPException(503, 'Interaction lookup failed. No complete result is available; please try again.') from exc
    return {**result, 'explanation': gemini_service.explain_analysis(result)}


@app.get('/api/health')
def health():
    try:
        rows = service.connect_graph().query('MATCH (d:Drug) RETURN count(d)').result_set
        if not rows or rows[0][0] == 0:
            raise RuntimeError('Drug graph is empty')
        return {'ok': True, 'graph': service.GRAPH_NAME, 'drugs': rows[0][0]}
    except Exception as exc:
        raise HTTPException(503, 'FalkorDB is unavailable') from exc


@app.get('/api/patients')
def patients():
    graph = service.connect_graph()
    ids = graph.query("MATCH (p:Patient) WHERE p.id STARTS WITH 'PAT' RETURN DISTINCT p.id ORDER BY p.id").result_set
    return [service.get_patient_context(graph, row[0]) for row in ids]


@app.get('/api/drugs')
def drugs(q: str = ''):
    if len(q.strip()) < 2:
        return []
    from medication_aliases import alias_index, REGIONAL_NAMES
    key = q.strip().lower()
    synonyms = {**alias_index()['aliases'], **REGIONAL_NAMES}
    matching = sorted(name for name in synonyms if name.startswith(key))[:10]
    canonical = [row[0] for row in service.connect_graph().query(
        'MATCH (d:Drug)-[:INTERACTS_WITH]-(:Drug) WHERE d.name_lower STARTS WITH $q '
        'RETURN DISTINCT d.name_lower ORDER BY d.name_lower LIMIT 10', {'q': key},
    ).result_set]
    return list(dict.fromkeys(matching + canonical))[:20]


@app.post('/api/check')
@app.post('/prescriptions/check')
def check(request: CheckRequest):
    return service.check_prescription(service.connect_graph(), request.patient_id, request.drug)


@app.get('/api/patients/{patient_id}/medications')
@app.get('/patients/{patient_id}/medications')
def medications(patient_id: str):
    return service.get_patient_context(service.connect_graph(), patient_id)


@app.post('/api/patients/{patient_id}/meds')
def add_medication(patient_id: str, request: MedicationRequest):
    return service.change_patient_medication(service.connect_graph(), patient_id, request.drug, 'add')


@app.delete('/api/patients/{patient_id}/meds/{drug}')
def remove_medication(patient_id: str, drug: str):
    return service.change_patient_medication(service.connect_graph(), patient_id, drug, 'remove')


@app.post('/patients/seed-demo')
def seed():
    return seed_demo_patients(service.connect_graph())


# Register after API routes so the frontend cannot shadow API endpoints.
dist = Path(__file__).parent / 'frontend' / 'dist'
if dist.is_dir():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
