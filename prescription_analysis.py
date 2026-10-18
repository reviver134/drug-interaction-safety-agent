"""Read-only all-pairs analysis; never creates patient records for uploads."""
from itertools import combinations
import json
import re

import prescription_service as service
from medication_aliases import alias_index

PATIENT_PREFIX = 'MATCH (p:Patient {id: $patient_id})-[:TAKES]->(:Prescription)-[:OF_DRUG]->(cur:Drug)'
PAIR_QUERY = service.PRESCRIPTION_PATHS_QUERY.replace(
    PATIENT_PREFIX, 'MATCH (cur:Drug {rxcui: $current_rxcui})'
)


def resolve_medication(graph, name):
    """Resolve exact generic/alias names. Partial and ambiguous matches stay unresolved."""
    normalized = ' '.join(name.strip().lower().split())
    index = alias_index()
    if normalized in index['combination_brands']:
        ids = index['aliases'][normalized]
        rows = graph.query('MATCH (d:Drug) WHERE d.rxcui IN $ids RETURN d.rxcui, d.name, d.name_lower', {'ids': ids}).result_set
        if len(rows) == len(ids):
            ingredients = [{'rxcui': row[0], 'name': row[1], 'name_lower': row[2], 'match': 'combination_brand'} for row in sorted(rows, key=lambda row: str(row[0]))]
            return {'input': name, 'status': 'RESOLVED', 'ingredients': ingredients}
        return {'input': name, 'status': 'UNRESOLVED', 'ingredients': [],
                'message': 'This brand contains ingredients missing from the loaded graph. Enter and verify its ingredients separately.'}
    resolved = service.resolve_drug(graph, normalized)
    if resolved:
        # Combination concepts are expanded, never collapsed into a single ingredient.
        canonical = resolved['name_lower']
        if ' / ' in canonical:
            names = canonical.split(' / ')
            ingredients = [service.resolve_drug(graph, item) for item in names]
            if all(ingredients):
                return {'input': name, 'status': 'RESOLVED', 'ingredients': ingredients}
            return {'input': name, 'status': 'UNRESOLVED', 'ingredients': [],
                    'message': 'Some ingredients of this combination could not be matched. Enter each ingredient separately.'}
        return {'input': name, 'status': 'RESOLVED', 'ingredients': [resolved]}
    # Explicit user-confirmed generic combinations, including regional synonyms.
    names = [s.strip() for s in re.split(r'\s*[/+]\s*', normalized)]
    if len(names) > 1 and all(names):
        ingredients = [service.resolve_drug(graph, item) for item in names]
        if all(ingredients):
            return {'input': name, 'status': 'RESOLVED', 'ingredients': ingredients}
    return {'input': name, 'status': 'UNRESOLVED', 'ingredients': [],
            'message': 'No unique exact name or alias match. Check the spelling or enter the active ingredients.'}


def resolve_list(graph, names):
    if not 1 <= len(names) <= 20:
        raise ValueError('Enter between 1 and 20 medications.')
    if any(not isinstance(name, str) or not name.strip() or len(name) > 160 for name in names):
        raise ValueError('Medication names must contain between 1 and 160 characters.')
    return [resolve_medication(graph, name) for name in names]


def analyze_medications(graph, names):
    medications = resolve_list(graph, names)
    ingredients = {}
    for med in medications:
        for ingredient in med['ingredients']:
            ingredients[str(ingredient['rxcui'])] = ingredient
    if len(ingredients) > 30:
        raise ValueError('This prescription expands to more than 30 ingredients. Split it into smaller lists.')
    pairs, evidence = [], []
    for number, (left, right) in enumerate(combinations(ingredients.values(), 2), 1):
        rows = graph.query(PAIR_QUERY, {'current_rxcui': left['rxcui'], 'proposed_rxcui': right['rxcui']}).result_set
        paths = service.parse_evidence_rows(rows)
        # Original source IDs remain attached; unique citation IDs distinguish pair paths.
        for path in paths:
            source_id = path.get('evidence_id')
            path.update({'source_evidence_id': source_id, 'evidence_id': f'E{len(evidence) + 1}'})
            path['source_dataset_url'] = ('https://ddinter.scbdd.com/' if path['kind'] == 'ddinter'
                else 'https://www.fda.gov/drugs/drug-interactions-labeling/healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems')
            path['source_link_scope'] = 'Dataset reference; a per-record source URL was not supplied by the loader.'
            evidence.append(path)
        decision = service.explain_evidence({'status': 'OK', 'paths': paths})
        pairs.append({'pair_id': f'P{number}', 'drugs': [left['name'], right['name']],
                      'rxcuis': [left['rxcui'], right['rxcui']],
                      'verdict': decision['verdict'], 'summary': decision['summary'],
                      'explanation': decision['explanation'], 'paths': paths})
    # Condition warnings are drug-level context, never evidence of a pair interaction.
    label_rows = graph.query('''MATCH (d:Drug)-[r:CONTRAINDICATED_IN]->(c:Condition)
        WHERE d.rxcui IN $ids OR d.name_lower IN $names
        RETURN DISTINCT d.rxcui, d.name, c.name, r.source, r.evidence_section, r.text_snippet
        ORDER BY d.name, c.name''', {'ids': [d['rxcui'] for d in ingredients.values()],
                                  'names': [' '.join(name.strip().lower().split()) for name in names]}).result_set
    label_context = [dict(zip(('rxcui', 'drug', 'condition', 'source', 'section', 'text_snippet'), row))
                     for row in label_rows]
    # Only match an entire confirmed medication's ingredient set. A warning for
    # a combination product must never become a warning for one component alone.
    label_keys = sorted({'|'.join(sorted(str(d['rxcui']) for d in med['ingredients']))
                         for med in medications if med['ingredients']})
    description_rows = graph.query('''MATCH (l:OpenFDALabel)
        WHERE l.key IN $keys
        RETURN l.key, l.ingredient_ids, l.set_id, l.record_id, l.effective_time,
               l.source, l.source_url, l.sections_json ORDER BY l.key''',
        {'keys': label_keys}).result_set
    label_descriptions = []
    for row in description_rows:
        label = dict(zip(('ingredient_key', 'ingredient_ids', 'set_id', 'record_id',
                          'effective_time', 'source', 'source_url', 'sections_json'), row))
        label['sections'] = json.loads(label.pop('sections_json'))
        for section in label['sections']:
            section['evidence_id'] = f"L{len(label_descriptions) + 1}-{section['section']}"
        label['drug'] = ' / '.join(ingredients[str(i)]['name'] for i in label['ingredient_ids'])
        label_descriptions.append(label)
    unresolved = [m['input'] for m in medications if m['status'] != 'RESOLVED']
    missing = [p['pair_id'] for p in pairs if not p['paths']]
    unknown_severity = [p['pair_id'] for p in pairs if any(
        item['kind'] == 'ddinter' and str(item.get('strength') or '').lower() not in {'major', 'moderate', 'minor'}
        for item in p['paths'])]
    return {'medications': medications, 'ingredients': list(ingredients.values()),
            'pairs': pairs, 'evidence': evidence, 'label_context': label_context,
            'label_descriptions': label_descriptions,
            'coverage': {'unique_ingredients': len(ingredients), 'checked_pairs': len(pairs),
                         'pairs_without_evidence': missing, 'unknown_severity_pairs': unknown_severity,
                         'unresolved_medications': unresolved,
                         'complete': not unresolved and not missing and not unknown_severity and bool(pairs)},
            'notice': 'Missing evidence does not establish safety. Pair checks use loaded DDInter, FDA enzyme and transporter data. OpenFDA condition warnings are shown separately; health conditions were not assessed.'}
