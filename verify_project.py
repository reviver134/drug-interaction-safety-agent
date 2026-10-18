"""Integration checks against the running demo graph: python verify_project.py."""
import asyncio
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from api import app
import prescription_service as service

ROOT = Path(__file__).resolve().parent


def data(result):
    if getattr(result, 'isError', getattr(result, 'is_error', False)):
        raise AssertionError(result)
    structured = getattr(result, 'structuredContent', getattr(result, 'structured_content', None))
    if structured is not None:
        return structured
    return json.loads(next(item.text for item in result.content if hasattr(item, 'text')))


def comparable(result):
    return {key: result[key] for key in ('status', 'verdict', 'paths', 'decision_basis', 'summary')}


async def main():
    graph = service.connect_graph()
    client = TestClient(app)
    manifest = json.loads((ROOT / 'demo_scenarios.json').read_text())
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'mcp_server.py')], env=os.environ.copy())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for scenario in manifest['scenarios']:
                if not scenario['enabled']:
                    continue
                pid, drug = scenario['patient_id'], scenario['proposed_drug']
                expected = service.check_prescription(graph, pid, drug)
                for route in ('/api/check', '/prescriptions/check'):
                    response = client.post(route, json={'patient_id': pid, 'drug': drug})
                    assert response.status_code == 200
                    assert comparable(response.json()) == comparable(expected)
                mcp_result = data(await session.call_tool('check_prescription', {'patient_id': pid, 'proposed_drug': drug}))
                assert comparable(mcp_result) == comparable(expected)
            for pid, drug, verdict in [('PAT001', 'not_a_real_drug_name_999', 'UNKNOWN_DRUG'), ('PAT999', 'simvastatin', 'UNKNOWN_PATIENT')]:
                http = client.post('/api/check', json={'patient_id': pid, 'drug': drug}).json()
                tool = data(await session.call_tool('check_prescription', {'patient_id': pid, 'proposed_drug': drug}))
                assert http['verdict'] == tool['verdict'] == verdict

            batch_names = ['clarithromycin', 'simvastatin', 'warfarin', 'paracetamol']
            from prescription_analysis import analyze_medications
            batch = analyze_medications(graph, batch_names)
            assert batch['coverage']['checked_pairs'] == 6
            http_batch = client.post('/api/prescription/analyze', json={'medications': batch_names, 'confirmed': True}).json()
            mcp_batch = data(await session.call_tool('analyze_prescription', {'medications': batch_names, 'confirmed': True}))
            for key in ('pairs', 'medications', 'coverage'):
                assert http_batch[key] == mcp_batch[key] == batch[key]
    patients = client.get('/api/patients').json()
    assert patients
    assert client.get('/api/drugs?q=simva').json()
    assert client.get('/api/health').json()['ok']
    if (ROOT / 'frontend/dist').exists():
        assert client.get('/').status_code == 200
    print('PASS: legacy HTTP/MCP consistency, all-pairs prescription HTTP/MCP consistency, aliases, unknown inputs, UI serving. Graph was read only.')


if __name__ == '__main__':
    asyncio.run(main())
