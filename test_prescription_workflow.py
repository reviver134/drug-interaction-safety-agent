"""Workflow checks with controlled Gemini responses and read-only live graph tests."""
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from api import app
import gemini_service as gemini
import prescription_analysis as analysis
from prescription_service import connect_graph


def photo():
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.graph = connect_graph()

    def test_extraction_image_validation_and_structured_output(self):
        extracted = gemini.Extraction(medications=[gemini.ExtractedMedication(
            name='paracetamol', strength='500 mg', formulation='tablet', uncertain=True,
            note='Check the handwriting.')], warnings=['Review every entry.'])
        with patch.object(gemini, 'generate', return_value=extracted) as generate:
            result = self.client.post('/api/prescription/extract', content=photo(), headers={'content-type': 'image/png'})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()['medications'][0]['uncertain'])
            self.assertEqual(result.json()['medications'][0]['name'], 'paracetamol')
            self.assertEqual(generate.call_count, 1)
            rejected = self.client.post('/api/prescription/extract', content=b'not a photo', headers={'content-type': 'image/png'})
            self.assertEqual(rejected.status_code, 422)
            self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.client.post('/api/prescription/extract', content=b'hello', headers={'content-type': 'text/plain'}).status_code, 415)
        self.assertEqual(self.client.post('/api/prescription/extract', content=b'x' * (8*1024*1024+1), headers={'content-type': 'image/png'}).status_code, 413)

    def test_extraction_failure_is_actionable(self):
        with patch.object(gemini, 'generate', side_effect=gemini.GeminiUnavailable('Gemini unavailable; enter names manually.')):
            result = self.client.post('/api/prescription/extract', content=photo(), headers={'content-type': 'image/png'})
            self.assertEqual(result.status_code, 503)
            self.assertIn('manually', result.json()['detail'])

    def test_aliases_combinations_and_duplicates(self):
        for name in ['paracetamol', 'tylenol']:
            result = analysis.resolve_medication(self.graph, name)
            self.assertEqual([str(d['rxcui']) for d in result['ingredients']], ['161'])
        for name in ['percocet', 'acetaminophen / oxycodone']:
            result = analysis.resolve_medication(self.graph, name)
            self.assertEqual({str(d['rxcui']) for d in result['ingredients']}, {'161', '7804'})
        result = analysis.analyze_medications(self.graph, ['paracetamol', 'acetaminophen', 'tylenol', 'warfarin'])
        self.assertEqual(result['coverage']['unique_ingredients'], 2)
        self.assertEqual(result['coverage']['checked_pairs'], 1)
        result = analysis.resolve_medication(self.graph, 'simva')
        self.assertEqual(result['status'], 'UNRESOLVED')

    def test_all_pairs_and_both_mechanism_directions(self):
        result = analysis.analyze_medications(self.graph, ['warfarin', 'fluconazole', 'rifampin'])
        self.assertEqual(result['coverage']['checked_pairs'], 3)
        paths = result['evidence']
        self.assertTrue(any(e.get('actor_drug') == 'fluconazole' and e.get('target') == 'CYP2C9' for e in paths))
        self.assertTrue(any(e.get('actor_drug') == 'rifampin' and e.get('relationship') == 'INDUCES' for e in paths))
        self.assertEqual(len({e['evidence_id'] for e in paths}), len(paths))

    def test_missing_data_unresolved_and_no_safety_claim(self):
        result = analysis.analyze_medications(self.graph, ['paracetamol', 'not_a_real_drug_name_999'])
        with patch.object(gemini, 'configured', return_value=True), patch.object(gemini, 'generate', side_effect=gemini.GeminiUnavailable('Unavailable')) as generate:
            explanation = gemini.explain_analysis(result)
            generate.assert_called_once()
            self.assertTrue(explanation['label_findings'])
        self.assertEqual(result['coverage']['checked_pairs'], 0)
        self.assertFalse(result['coverage']['complete'])
        self.assertTrue(any('not checked' in gap for gap in explanation['data_gaps']))
        # An empty graph-result pair is missing evidence, not a low-risk verdict.
        class EmptyPaths:
            def query(_, cypher, params):
                if 'UNION' in cypher:
                    return type('Result', (), {'result_set': []})()
                return self.graph.query(cypher, params)
        result = analysis.analyze_medications(EmptyPaths(), ['clarithromycin', 'simvastatin'])
        self.assertEqual(result['pairs'][0]['verdict'], 'NO_KNOWN_INTERACTION')
        self.assertIn('No interaction data', gemini.explain_analysis(result)['data_gaps'][0])

    def test_transporter_evidence_and_separate_label_context(self):
        result = analysis.analyze_medications(self.graph, ['metformin', 'cimetidine'])
        self.assertTrue(any(e.get('target_type') == 'Transporter' and e.get('target') == 'OCT2'
                            for e in result['evidence']))
        result = analysis.analyze_medications(self.graph, ['amoxicillin / clavulanate'])
        self.assertTrue(result['label_context'])
        self.assertTrue(all(e['kind'] in {'ddinter', 'mechanism'} for e in result['evidence']))

    def test_plain_language_severity_fallback(self):
        result = analysis.analyze_medications(self.graph, ['warfarin', 'aspirin'])
        with patch.object(gemini, 'configured', return_value=False):
            text = gemini.explain_analysis(result)['findings'][0]['explanation']
        self.assertIn('pharmacist or prescriber', text.lower())
        self.assertIn('no description of the specific effect', text)
        self.assertNotIn('BLOCK', text)

    def test_rxnorm_label_descriptions_and_combination_identity(self):
        for name, ingredient in [('warfarin', '11289'), ('metformin', '6809'), ('paracetamol', '161')]:
            result = analysis.analyze_medications(self.graph, [name])
            self.assertTrue(result['label_descriptions'], name)
            label = result['label_descriptions'][0]
            self.assertEqual(label['ingredient_ids'], [ingredient])
            self.assertTrue(label['set_id'])
            self.assertTrue(label['source_url'].startswith('https://dailymed.nlm.nih.gov/'))
            self.assertTrue(label['sections'])
        combination = analysis.analyze_medications(self.graph, ['amoxicillin / clavulanate'])
        self.assertTrue(combination['label_descriptions'])
        self.assertEqual(set(combination['label_descriptions'][0]['ingredient_ids']), {'723', '48203'})
        separate = analysis.analyze_medications(self.graph, ['amoxicillin', 'clavulanate'])
        self.assertFalse(any(len(label['ingredient_ids']) > 1 for label in separate['label_descriptions']))

    def test_gemini_receives_label_only_evidence_and_validates_warning_sources(self):
        result = analysis.analyze_medications(self.graph, ['warfarin'])
        label = result['label_descriptions'][0]
        sources = {s['section']: s['evidence_id'] for s in label['sections']}
        output = gemini.Explanation(findings=[], label_findings=[gemini.LabelFinding(
            ingredient_key=label['ingredient_key'],
            explanation='The source warns of bleeding with warfarin.',
            evidence_ids=[sources['boxed_warning']],
            warnings=[gemini.LabelWarning(text='The label includes a bleeding warning.',
                                         evidence_ids=[sources['boxed_warning']])])])
        with patch.object(gemini, 'configured', return_value=True), patch.object(gemini, 'generate', return_value=output) as generate:
            explanation = gemini.explain_analysis(result)
            self.assertEqual(explanation['source'], 'gemini')
            import json
            payload = json.loads(generate.call_args.args[0])
            sent_label = payload['openfda_labels'][0]
            self.assertEqual(sent_label['ingredient_key'], label['ingredient_key'])
            self.assertEqual(set(sent_label), {'ingredient_key', 'drug', 'sections'})
            self.assertFalse(any(s['section'] in {'description', 'indications_and_usage'}
                                 for s in sent_label['sections']))
            self.assertNotIn('data_gaps', payload)
            self.assertEqual(explanation['label_findings'][0]['warnings'][0]['evidence_ids'], [sources['boxed_warning']])
            # Descriptions cannot substantiate warning claims.
            output.label_findings[0].warnings[0].evidence_ids = [sources['description']]
            self.assertEqual(gemini.explain_analysis(result)['source'], 'template')
            output.label_findings[0].warnings[0].evidence_ids = ['L999-warnings']
            self.assertEqual(gemini.explain_analysis(result)['source'], 'template')
            output.label_findings[0].ingredient_key = 'unknown'
            self.assertEqual(gemini.explain_analysis(result)['source'], 'template')

    def test_gemini_not_called_without_any_evidence(self):
        result = analysis.analyze_medications(self.graph, ['not_a_real_drug_999'])
        with patch.object(gemini, 'configured', return_value=True), patch.object(gemini, 'generate') as generate:
            explanation = gemini.explain_analysis(result)
            generate.assert_not_called()
            self.assertEqual(explanation['label_findings'], [])

    def test_grounded_explanation_and_bad_citation_fallback(self):
        result = analysis.analyze_medications(self.graph, ['clarithromycin', 'simvastatin'])
        valid = gemini.Explanation(findings=[gemini.Finding(pair_id='P1', explanation='The loaded source reports a major interaction.', evidence_ids=['E1'])])
        with patch.object(gemini, 'configured', return_value=True), patch.object(gemini, 'generate', return_value=valid) as generate:
            self.assertEqual(gemini.explain_analysis(result)['source'], 'gemini')
            import json
            payload = json.loads(generate.call_args.args[0])
            path = payload['pairs'][0]['paths'][0]
            self.assertEqual(path['evidence_id'], 'E1')
            self.assertIn('strength', path)
            self.assertNotIn('source_dataset_url', path)
            self.assertNotIn('current_rxcui', path)
            self.assertTrue(all(p['paths'] for p in payload['pairs']))
            valid.findings[0].evidence_ids = ['E999']
            self.assertEqual(gemini.explain_analysis(result)['source'], 'template')
            valid.findings[0].evidence_ids = ['E1']
            valid.findings[0].explanation = 'This combination is safe.'
            self.assertEqual(gemini.explain_analysis(result)['source'], 'template')

    def test_confirmation_validation_and_database_failure(self):
        body = {'medications': ['clarithromycin', 'simvastatin'], 'confirmed': False}
        self.assertEqual(self.client.post('/api/prescription/analyze', json=body).status_code, 422)
        for names in [[], [''], ['a'] * 21]:
            self.assertEqual(self.client.post('/api/prescription/resolve', json={'medications': names}).status_code, 422)
        with patch.object(analysis, 'analyze_medications', side_effect=RuntimeError('Graph disconnected')):
            body['confirmed'] = True
            self.assertEqual(self.client.post('/api/prescription/analyze', json=body).status_code, 503)


if __name__ == '__main__':
    unittest.main()
