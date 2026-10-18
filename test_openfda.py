"""RxNorm direction, multi-hop identity and OpenFDA source contract checks."""
import json
from pathlib import Path
import tempfile
import unittest

from rxnorm_ingredients import build_mapping
from session4_openfda import extract_label, MAX_TEXT


class MappingTests(unittest.TestCase):
    def test_direction_components_combinations_and_excluded_links(self):
        def concept(key, tty):
            c = [''] * 18
            c[0], c[11], c[12], c[16] = key, 'RXNORM', tty, 'N'
            return '|'.join(c) + '\n'

        def relation(target, source, rel, suppressed='N'):
            c = [''] * 16
            c[0], c[4], c[7], c[10], c[14] = target, source, rel, 'RXNORM', suppressed
            return '|'.join(c) + '\n'

        with tempfile.TemporaryDirectory() as tmp:
            conso, rel = Path(tmp) / 'conso', Path(tmp) / 'rel'
            conso.write_text(''.join(concept(k, t) for k, t in [
                ('a', 'IN'), ('b', 'IN'), ('salt', 'PIN'), ('component', 'SCDC'),
                ('product', 'SCD'), ('combo', 'MIN'), ('brand', 'BN')]), encoding='utf-8')
            rel.write_text(''.join([
                relation('a', 'salt', 'form_of'),
                relation('salt', 'component', 'has_precise_ingredient'),
                relation('component', 'product', 'consists_of'),
                relation('a', 'combo', 'has_part'),
                relation('b', 'combo', 'has_part'),
                relation('combo', 'combination_product', 'has_ingredients'),
                relation('b', 'product', 'isa'),
                relation('b', 'product', 'has_ingredient', 'Y'),
                relation('brand', 'product', 'has_tradename'),
                relation('b', 'brand', 'tradename_of'),
            ]), encoding='utf-8')
            m = build_mapping(conso, rel)
        self.assertEqual(m['product'], ['a'])
        self.assertEqual(m['combination_product'], ['a', 'b'])
        self.assertEqual(m['salt'], ['a'])

    def test_source_sections_truncation_and_conflicting_identity(self):
        record = {'id': 'record', 'set_id': 'set', 'effective_time': '20261001',
                  'openfda': {'rxcui': ['product']},
                  'description': ['Product description'],
                  'warnings': ['x' * (MAX_TEXT + 1)]}
        label, reason = extract_label(record, {'product': ['a', 'b']}, {'a', 'b'})
        self.assertIsNone(reason)
        self.assertEqual(label['ingredient_ids'], ['a', 'b'])
        self.assertEqual(label['set_id'], 'set')
        self.assertTrue(json.loads(label['sections_json'])[1]['truncated'])
        record['openfda']['rxcui'].append('different')
        label, reason = extract_label(record, {'product': ['a', 'b'], 'different': ['a']}, {'a', 'b'})
        self.assertIsNone(label)
        self.assertEqual(reason, 'unmapped_or_conflicting_rxcui')


if __name__ == '__main__':
    unittest.main()
