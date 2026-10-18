"""Resolve products to every base IN ingredient using directed RxNorm links."""
import json
from collections import defaultdict
from settings import DATA_DIR


def build_mapping(conso, relations):
    types = {}
    with open(conso, encoding='utf-8') as stream:
        for line in stream:
            c = line.rstrip('\n').split('|')
            if (len(c) > 16 and c[11] == 'RXNORM' and c[16] != 'Y'
                    and c[12] in {'IN', 'PIN', 'MIN', 'BN', 'SCD', 'SBD', 'SCDC',
                                  'SBDC', 'SCDF', 'SBDF', 'SCDG', 'SBDG', 'GPCK', 'BPCK'}):
                types[c[0]] = c[12]
    edges = defaultdict(set)
    # RXNREL describes RXCUI2 -> RXCUI1. Never traverse dose-form,
    # hierarchy, pack or pharmacologic-class links as ingredient identity.
    forward = {'has_ingredient', 'has_ingredients', 'has_precise_ingredient', 'consists_of',
               'tradename_of', 'form_of'}
    inverse = {'ingredient_of', 'ingredients_of', 'precise_ingredient_of', 'constitutes',
               'has_tradename', 'has_form'}
    with open(relations, encoding='utf-8') as stream:
        for line in stream:
            c = line.rstrip('\n').split('|')
            if len(c) <= 14 or c[10] != 'RXNORM' or c[14] == 'Y':
                continue
            target, source, rel = c[0], c[4], c[7]
            # MIN decomposition is the only part/whole relation that denotes
            # ingredients. Pack contents and arbitrary parts are excluded.
            if rel == 'has_part' and types.get(source) == 'MIN' and types.get(target) in {'IN', 'PIN'}:
                edges[source].add(target)
                continue
            if rel == 'part_of' and types.get(target) == 'MIN' and types.get(source) in {'IN', 'PIN'}:
                edges[target].add(source)
                continue
            if rel in inverse:
                source, target = target, source
            elif rel not in forward:
                continue
            # A product's brand can span several formulations; don't walk from
            # a product to BN and collect ingredients of unrelated formulations.
            if types.get(source) == 'IN' or types.get(target) == 'BN':
                continue
            edges[source].add(target)
    resolved = {key: {key} for key, tty in types.items() if tty == 'IN'}
    while True:
        changed = False
        for source, targets in edges.items():
            found = set().union(*(resolved.get(t, set()) for t in targets))
            if found - resolved.get(source, set()):
                resolved.setdefault(source, set()).update(found)
                changed = True
        if not changed:
            break
    return {key: sorted(ids) for key, ids in sorted(resolved.items())}


def main():
    mapping = build_mapping(DATA_DIR / 'rrf/RXNCONSO.RRF', DATA_DIR / 'rrf/RXNREL.RRF')
    path = DATA_DIR / 'rxcui_to_ingredients.json'
    path.write_text(json.dumps(mapping, separators=(',', ':')), encoding='utf-8')
    print(f'Wrote {len(mapping)} concept mappings to {path}')


if __name__ == '__main__':
    main()
