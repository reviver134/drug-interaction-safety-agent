"""Opt-in live Gemini + graph test using a synthetic prescription image."""
import io
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from api import app
from settings import ROOT
import gemini_service


def main():
    report = {'timestamp': datetime.now(timezone.utc).isoformat(),
              'synthetic_image': True, 'graph_writes': False, 'checks': {}}
    try:
        assert gemini_service.configured(), 'Gemini key is not configured'
        image = Image.new('RGB', (1400, 850), 'white')
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype('arial.ttf', 38)
        except OSError:
            font = ImageFont.load_default(size=38)
        names = ['Clarithromycin', 'Simvastatin', 'Warfarin', 'Paracetamol']
        draw.text((60, 45), 'SYNTHETIC MEDICATION LIST', fill='black', font=font)
        draw.text((60, 110), 'Software test only - not a real prescription', fill='black', font=font)
        for i, name in enumerate(names):
            draw.text((80, 240 + i * 120), f'{i+1}. {name}', fill='black', font=font)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        client = TestClient(app)
        report['stage'] = 'live image extraction'
        extracted = client.post('/api/prescription/extract', content=buffer.getvalue(), headers={'content-type': 'image/png'})
        report['checks']['extraction_http_status'] = extracted.status_code
        if extracted.status_code != 200:
            raise AssertionError(extracted.json().get('detail', 'Image extraction failed'))
        medications = extracted.json()['medications']
        read_names = [m['name'] for m in medications]
        report['checks']['extracted_names'] = read_names
        assert len(read_names) == 4, 'Extraction did not return all four synthetic medications'
        assert not any(m['uncertain'] for m in medications), 'Clear printed test image was marked uncertain'
        report['stage'] = 'alias resolution'
        resolved = client.post('/api/prescription/resolve', json={'medications': read_names})
        assert resolved.status_code == 200, 'Medication resolution failed'
        mappings = resolved.json()['medications']
        assert all(m['status'] == 'RESOLVED' for m in mappings), 'Some extracted medications did not resolve'
        ids = {str(d['rxcui']) for m in mappings for d in m['ingredients']}
        assert ids == {'21212', '36567', '11289', '161'}, f'Unexpected resolved ingredient IDs: {sorted(ids)}'
        report['checks']['ingredient_ids'] = sorted(ids)
        report['stage'] = 'live graph and Gemini explanation'
        analyzed = client.post('/api/prescription/analyze', json={'medications': read_names, 'confirmed': True})
        assert analyzed.status_code == 200, 'All-pairs analysis failed'
        result = analyzed.json()
        assert result['coverage']['checked_pairs'] == 6
        assert not result['coverage']['unresolved_medications']
        major = next(p for p in result['pairs'] if set(map(str, p['rxcuis'])) == {'21212', '36567'})
        assert major['verdict'] == 'BLOCK'
        assert any(p['kind'] == 'ddinter' for p in major['paths'])
        assert any(p.get('target') == 'CYP3A4' for p in major['paths'])
        report['checks'].update({'checked_pairs': 6, 'evidence_paths': len(result['evidence']),
                                 'major_pair': major['drugs'],
                                 'explanation_source': result['explanation']['source'],
                                 'data_gaps': result['explanation']['data_gaps']})
        assert result['explanation']['source'] == 'gemini', result['explanation'].get('reason', 'Live explanation fell back')
        report['checks']['explanation'] = result['explanation']['findings']
        report['status'] = 'PASS'
        report.pop('stage', None)
    except Exception as exc:
        report['status'] = 'FAIL'
        report['error'] = str(exc)
    path = ROOT / 'verification/live_prescription_test.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
