"""Stream local OpenFDA archives into source-attributed medicine label context.

Run rxnorm_ingredients.py first. No live API call or patient data is needed.
Combination label text stays attached to the complete ingredient set.
"""
import json
import zipfile
from urllib.parse import quote

import ijson
from prescription_service import connect_graph
from settings import DATA_DIR

SECTIONS = ('description', 'indications_and_usage', 'contraindications',
            'boxed_warning', 'warnings', 'warnings_and_cautions', 'drug_interactions')
MAX_TEXT = 8000


def extract_label(record, mapping, known):
    raw_ids = (record.get('openfda') or {}).get('rxcui') or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    sets = {tuple(mapping[i]) for value in raw_ids if (i := str(value)) in mapping}
    # Conflicting product identities are audited rather than merged or guessed.
    if len(sets) != 1:
        return None, 'unmapped_or_conflicting_rxcui'
    ids = next(iter(sets))
    if not set(ids) <= known:
        return None, 'ingredient_missing_from_graph'
    sections = []
    for section in SECTIONS:
        raw = record.get(section) or []
        if isinstance(raw, str):
            raw = [raw]
        text = ' '.join(' '.join(t.split()) for t in raw if isinstance(t, str)).strip()
        if text:
            sections.append({'section': section, 'text': text[:MAX_TEXT],
                             'truncated': len(text) > MAX_TEXT})
    if not sections:
        return None, 'no_supported_sections'
    set_id = str(record.get('set_id') or '')
    if not set_id:
        return None, 'missing_set_id'
    return {'key': '|'.join(ids), 'ingredient_ids': list(ids),
            'set_id': set_id, 'record_id': str(record.get('id') or ''),
            'effective_time': str(record.get('effective_time') or ''),
            'source': 'openFDA',
            'source_url': 'https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=' + quote(set_id, safe=''),
            'sections_json': json.dumps(sections, ensure_ascii=False)}, None


def main():
    mapping = json.loads((DATA_DIR / 'rxcui_to_ingredients.json').read_text(encoding='utf-8'))
    graph = connect_graph()
    known = {str(r[0]) for r in graph.query('MATCH (d:Drug) RETURN d.rxcui').result_set}
    paths = sorted((DATA_DIR / 'openfda_labels').glob('drug-label-*.json.zip'))
    if not paths:
        raise FileNotFoundError('No local OpenFDA archives found')
    selected, stats = {}, {'records': 0, 'archives': len(paths)}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if not member.endswith('.json'):
                    continue
                with archive.open(member) as stream:
                    for record in ijson.items(stream, 'results.item'):
                        stats['records'] += 1
                        label, reason = extract_label(record, mapping, known)
                        if reason:
                            stats[reason] = stats.get(reason, 0) + 1
                            continue
                        previous = selected.get(label['key'])
                        # One representative product label per exact ingredient set;
                        # preserve whole sections from the same latest record.
                        rank = lambda x: (x['effective_time'], x['record_id'])
                        if previous is None or rank(label) > rank(previous):
                            selected[label['key']] = label
        print(f"Read {path.name}: {stats['records']} records, {len(selected)} ingredient sets", flush=True)
    rows = list(selected.values())
    for start in range(0, len(rows), 100):
        graph.query('''UNWIND $rows AS row
            MERGE (l:OpenFDALabel {key: row.key})
            SET l.ingredient_ids = row.ingredient_ids, l.set_id = row.set_id,
                l.record_id = row.record_id, l.effective_time = row.effective_time,
                l.source = row.source, l.source_url = row.source_url,
                l.sections_json = row.sections_json
            WITH l, row UNWIND row.ingredient_ids AS ingredient
            MATCH (d:Drug {rxcui: ingredient}) MERGE (d)-[:HAS_LABEL]->(l)''',
                    {'rows': rows[start:start + 100]})
    stats['label_ingredient_sets'] = len(rows)
    report = DATA_DIR / 'openfda_import_report.json'
    report.write_text(json.dumps(stats, indent=2), encoding='utf-8')
    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()
