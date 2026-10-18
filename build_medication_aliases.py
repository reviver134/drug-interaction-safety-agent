"""Build deployable exact-name aliases from local RxNorm concept/brand records."""
import json
from collections import defaultdict
from settings import DATA_DIR, ROOT


def main():
    ingredients, brands = {}, {}
    with (DATA_DIR / 'rrf/RXNCONSO.RRF').open(encoding='utf-8') as stream:
        for line in stream:
            c = line.rstrip('\n').split('|')
            if len(c) > 16 and c[1] == 'ENG' and c[11] == 'RXNORM' and c[16] != 'Y':
                if c[12] == 'IN':
                    ingredients[c[0]] = c[14].strip().lower()
                elif c[12] == 'BN':
                    brands[c[0]] = c[14].strip().lower()
    brand_ids = defaultdict(set)
    with (DATA_DIR / 'rrf/RXNREL.RRF').open(encoding='utf-8') as stream:
        for line in stream:
            c = line.rstrip('\n').split('|')
            if len(c) > 14 and c[10] == 'RXNORM' and c[14] != 'Y':
                # Preserve all ingredient endpoints, including multi-ingredient brands.
                if c[0] in brands and c[4] in ingredients and c[7] == 'has_tradename':
                    brand_ids[c[0]].add(c[4])
                elif c[4] in brands and c[0] in ingredients and c[7] == 'tradename_of':
                    brand_ids[c[4]].add(c[0])
    aliases = defaultdict(set)
    # Terms attached to the same ingredient concept are identities, not parent guesses.
    with (DATA_DIR / 'rrf/RXNCONSO.RRF').open(encoding='utf-8') as stream:
        for line in stream:
            c = line.rstrip('\n').split('|')
            if len(c) > 16 and c[1] == 'ENG' and c[16] != 'Y' and c[0] in ingredients:
                name = ' '.join(c[14].lower().split())
                if 1 < len(name) <= 120:
                    aliases[name].add(c[0])
    for brand, ids in brand_ids.items():
        aliases[brands[brand]].update(ids)
    payload = {'source': 'Local RxNorm RXNCONSO ingredient synonyms and RXNREL IN/BN tradename links',
               'aliases': {name: sorted(ids) for name, ids in sorted(aliases.items())},
               'combination_brands': sorted({brands[b] for b, ids in brand_ids.items() if len(ids) > 1})}
    (ROOT / 'medication_aliases.json').write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'Built {len(aliases)} aliases; {len(payload["combination_brands"])} combination brands')


if __name__ == '__main__':
    main()
