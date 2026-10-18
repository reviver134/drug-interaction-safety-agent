"""Reviewed generic synonyms; IDs continue to come from the graph."""
import json
from functools import lru_cache
from pathlib import Path

REGIONAL_NAMES = {
    'paracetamol': 'acetaminophen',
}


@lru_cache(maxsize=1)
def alias_index():
    return json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
