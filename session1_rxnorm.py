"""
session1_rxnorm.py
==================
Session 1 — RxNorm Drug Node Spine

Loads canonical Drug nodes into FalkorDB from RxNorm RRF files.

Key design rules enforced:
  Rule 1 — Case normalization : every name is stored as name_lower (lowercase+strip)
  Rule 2 — Ingredient dedup   : one Drug node per RXCUI at IN (ingredient) level;
                                 brand names / dose forms become aliases on the same node
  Rule 3 — Health checks      : automated assertions run after every major step;
                                 script raises on any failure so bad data is never silently accepted

Outputs:
  • Drug nodes in FalkorDB graph 'drug_interactions'
  • name_to_rxcui.json  — shared lookup map used by Sessions 2-5
  • session1.log        — stats summary
"""

import json
import logging
import math
import os
import time
from collections import defaultdict
from falkordb import FalkorDB

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
from settings import DATA_DIR, HOST, PORT, GRAPH_NAME
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

RRF_DIR       = str(DATA_DIR / "rrf")
RXNCONSO_FILE = os.path.join(RRF_DIR, "RXNCONSO.RRF")
RXNREL_FILE   = os.path.join(RRF_DIR, "RXNREL.RRF")
RXNSAT_FILE   = os.path.join(RRF_DIR, "RXNSAT.RRF")

LOOKUP_MAP_FILE = str(DATA_DIR / "name_to_rxcui.json")
RXCUI_INGREDIENT_MAP_FILE = str(DATA_DIR / "rxcui_to_ingredient.json")
LOG_FILE        = str(LOG_DIR / "session1.log")

FALKORDB_HOST = HOST
FALKORDB_PORT = PORT   # Redis/FalkorDB protocol port (3000 is the browser UI port)


BATCH_SIZE = 1000   # Drug nodes per query

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# RULE 1 — Case / whitespace normalization
# ─────────────────────────────────────────────
def normalize_name(raw: str) -> str:
    """Strip leading/trailing whitespace and lowercase.
    Applied to every drug name string from every source."""
    return raw.strip().lower()


# ─────────────────────────────────────────────
# STEP A — Parse RXNCONSO.RRF
#
# Column layout (pipe-delimited, 0-indexed):
#   0  RXCUI
#   1  LAT
#   11 SAB   (source vocabulary)
#   12 TTY   (term type)
#   14 STR   (string / drug name)
#
# TTY reference used here:
#   IN   = Ingredient           -> primary node
#   PIN  = Precise Ingredient   -> alias of its parent IN
#   BN   = Brand Name           -> alias
#   MIN  = Multi-Ingredient     -> kept separately (complex drugs)
#   SCD, SBD, SCDC, SBDC, ...  -> clinical dose forms -> alias only
# ─────────────────────────────────────────────
INGREDIENT_TTYS = {"IN", "MIN"}      # Create node for these
ALIAS_TTYS      = {                  # Treat as aliases (name variants)
    "PIN", "BN",
    "SCD", "SBD", "SCDC", "SBDC",
    "SCDF", "SBDF", "SCDG", "SBDG",
    "SCDFP", "SBDFP", "SCDGP",
    "GPCK", "BPCK",
    "SY", "TMSY", "PSN",
}

def parse_rxnconso(filepath: str):
    """
    Returns:
      rxcui_to_canonical : dict  {rxcui: (display_name, name_lower)}
                           Only IN/MIN rows. One entry per RXCUI.
      rxcui_to_aliases   : defaultdict(set)  {rxcui: {alias_lower, ...}}
      rxcui_to_unii      : dict {rxcui: unii_string} from MTHSPL SU rows.
    """
    import re

    rxcui_to_canonical = {}   # rxcui -> (display, lower)
    rxcui_to_aliases   = defaultdict(set)
    rxcui_to_unii      = {}

    stats = {"total": 0, "ingredient": 0, "alias": 0, "unii": 0}
    t0 = time.time()

    with open(filepath, encoding="utf-8") as fh:
        for line in fh:
            stats["total"] += 1
            cols = line.rstrip("\n").split("|")

            if len(cols) < 15:
                continue

            rxcui = cols[0].strip()
            lat   = cols[1].strip()
            sab   = cols[11].strip()
            tty   = cols[12].strip()
            code  = cols[13].strip()
            name  = cols[14].strip()

            if lat != "ENG":
                continue

            name_lower = normalize_name(name)

            # 1. Canonical ingredients from RXNORM
            if sab == "RXNORM" and tty in INGREDIENT_TTYS:
                if rxcui not in rxcui_to_canonical or tty == "IN":
                    rxcui_to_canonical[rxcui] = (name, name_lower)
                stats["ingredient"] += 1

            # 2. Extract UNII from MTHSPL (Substance rows)
            if sab == "MTHSPL" and tty == "SU":
                if code and rxcui not in rxcui_to_unii:
                    rxcui_to_unii[rxcui] = code
                    stats["unii"] += 1

            # 3. Collect alias names from ingredient / name-variant rows only (not every RXNORM product string)
            if tty in INGREDIENT_TTYS or tty in ALIAS_TTYS:
                if len(name_lower) >= 2 and len(name_lower) <= 120:
                    rxcui_to_aliases[rxcui].add(name_lower)
                    # Check bracketed brand names e.g. "SIMVASTATIN ... [ZOCOR]"
                    m = re.search(r"\[(.*?)\]", name)
                    if m:
                        brand = normalize_name(m.group(1))
                        if brand:
                            rxcui_to_aliases[rxcui].add(brand)
                    stats["alias"] += 1

    elapsed = time.time() - t0
    log.info(
        "RXNCONSO parsed in %.1fs | total=%d | ingredients=%d | UNIIs=%d | alias terms=%d",
        elapsed, stats["total"], len(rxcui_to_canonical), len(rxcui_to_unii), stats["alias"],
    )
    return rxcui_to_canonical, rxcui_to_aliases, rxcui_to_unii


def parse_rxnsat_atc(filepath: str, ingredient_rxcuis: set) -> dict:
    """Read ATC attributes when the local RxNorm snapshot includes them."""
    rxcui_to_atc = {}
    if not os.path.exists(filepath):
        log.warning("RXNSAT file missing; ATC codes will not be populated: %s", filepath)
        return rxcui_to_atc

    with open(filepath, encoding="utf-8") as fh:
        for line in fh:
            cols = line.rstrip("\n").split("|")
            if len(cols) < 11 or cols[8].strip() != "ATC" or cols[9].strip() != "ATC":
                continue
            rxcui = cols[0].strip()
            atc_code = cols[10].strip()
            if rxcui in ingredient_rxcuis and atc_code:
                rxcui_to_atc.setdefault(rxcui, atc_code)

    if rxcui_to_atc:
        log.info("RXNSAT ATC attributes: %d ingredient mappings", len(rxcui_to_atc))
    else:
        log.warning("No ATC attributes found in %s; Drug.atc_code will remain unset", filepath)
    return rxcui_to_atc



# Curated high-profile clinical brand aliases to guarantee immediate lookup
CURATED_BRAND_ALIASES = {
    "26225": {"zofran", "zuplenz"},                       # ondansetron
    "36567": {"zocor", "flolipid"},                        # simvastatin
    "11289": {"coumadin", "jantoven"},                     # warfarin
    "21212": {"biaxin", "clarithromycin"},                 # clarithromycin
    "4450":  {"diflucan"},                                 # fluconazole
    "6809":  {"glucophage", "fortamet", "glumetza"},       # metformin
    "36437": {"zoloft"},                                   # sertraline
    "10689": {"ultram", "conzip"},                         # tramadol
    "703":   {"cordarone", "pacerone", "nexterone"},       # amiodarone
    "3407":  {"lanoxin", "digitek"},                       # digoxin
    "9384":  {"rifadin", "rimactane", "rifampicin"},       # rifampin
    "1191":  {"bayer", "ecotrin", "acetylsalicylic acid"}, # aspirin
    "435":   {"proair", "ventolin", "proventil", "salbutamol"}, # albuterol
}


# ─────────────────────────────────────────────
# STEP B — Parse RXNREL.RRF for child -> ingredient mapping
# ─────────────────────────────────────────────
def parse_rxnrel_child_to_in(filepath: str, canonical_ingredients: set) -> dict:
    """Compatibility map for unique ingredients or exact canonical combinations.

    The full one-to-many mapping is persisted separately for label imports.
    Ambiguous products are never collapsed to the first ingredient encountered.
    """
    from rxnorm_ingredients import build_mapping
    mapping = build_mapping(RXNCONSO_FILE, filepath)
    output = DATA_DIR / 'rxcui_to_ingredients.json'
    output.write_text(json.dumps(mapping, separators=(',', ':')), encoding='utf-8')
    canonical_by_set = defaultdict(list)
    for rxcui in canonical_ingredients:
        if rxcui in mapping:
            canonical_by_set[tuple(mapping[rxcui])].append(rxcui)
    result = {}
    for product, ids in mapping.items():
        candidates = canonical_by_set.get(tuple(ids), [])
        if len(candidates) == 1 and product not in canonical_ingredients:
            result[product] = candidates[0]
    return result


# ─────────────────────────────────────────────
# STEP C — Merge aliases using child->IN map
# ─────────────────────────────────────────────
def merge_aliases(rxcui_to_canonical, rxcui_to_aliases, child_to_in, rxcui_to_unii):
    """
    Merges aliases from child RXCUIs into their canonical ingredient sets.
    Also propagates child UNIIs if parent was missing one, and attaches
    curated high-profile clinical brand aliases.
    """
    extra_merges = 0
    for child_rxcui, names in list(rxcui_to_aliases.items()):
        if child_rxcui in child_to_in:
            parent = child_to_in[child_rxcui]
            if parent in rxcui_to_canonical and parent != child_rxcui:
                rxcui_to_aliases[parent].update(names)
                # Also propagate UNII if parent lacked one
                if child_rxcui in rxcui_to_unii and parent not in rxcui_to_unii:
                    rxcui_to_unii[parent] = rxcui_to_unii[child_rxcui]
                extra_merges += 1

    # Attach curated brand aliases
    for rxcui, brands in CURATED_BRAND_ALIASES.items():
        if rxcui in rxcui_to_canonical:
            rxcui_to_aliases[rxcui].update(brands)

    log.info("Alias merge: %d child RXCUIs merged into parent ingredients; curated brands attached.", extra_merges)


# ─────────────────────────────────────────────
# STEP D — Build the shared lookup map
#
# name_to_rxcui: {name_lower -> rxcui}
# Covers:
#   * canonical ingredient names
#   * all alias names (brand names, dose form names, synonyms)
# Collision: if two different drugs share the same alias string,
#            the ingredient (IN) wins; log a warning.
# ─────────────────────────────────────────────
def build_lookup_map(rxcui_to_canonical, rxcui_to_aliases):
    """
    Returns name_to_rxcui dict keyed on lowercase drug name strings.
    """
    name_to_rxcui = {}
    collisions = 0

    # First pass: canonical names (highest priority - no override)
    for rxcui, (display, name_lower) in rxcui_to_canonical.items():
        if name_lower in name_to_rxcui and name_to_rxcui[name_lower] != rxcui:
            log.warning(
                "Name collision (canonical): '%s' maps to both %s and %s - keeping first",
                name_lower, name_to_rxcui[name_lower], rxcui,
            )
            collisions += 1
        else:
            name_to_rxcui[name_lower] = rxcui

    # Second pass: clean alias names only (same filter as Drug.aliases on insert)
    for rxcui, aliases in rxcui_to_aliases.items():
        if rxcui not in rxcui_to_canonical:
            continue   # Skip non-ingredient RXCUIs
        _, canonical_lower = rxcui_to_canonical[rxcui]
        for alias in aliases:
            if not is_clean_alias(alias, canonical_lower):
                continue
            if alias not in name_to_rxcui:
                name_to_rxcui[alias] = rxcui
            elif name_to_rxcui[alias] != rxcui:
                collisions += 1

    log.info("Lookup map built: %d entries, %d name collisions (kept first winner).", len(name_to_rxcui), collisions)
    return name_to_rxcui


# ─────────────────────────────────────────────
# STEP E — FalkorDB insertion
# ─────────────────────────────────────────────
def is_clean_alias(alias: str, canonical_name_lower: str) -> bool:
    """Filter out noisy dosage-strength and product concepts from aliases.
    Keep only genuine human/clinician names (brand names, salts, INN synonyms)."""
    import re
    a = alias.strip().lower()
    if not a or a == canonical_name_lower:
        return False
    # Reject strings containing any digits (dosages like "1 mg", "10 mg", "0.8 mg/ml", "100 mcg", "1%")
    if re.search(r"\d", a):
        return False
    # Reject strings with clinical product/packaging boilerplate
    boilerplate = [
        "oral product", "injectable product", "liquid product", "oral tablet",
        "oral capsule", " pill", "prefilled syringe", "oral solution",
        "injectable solution", "oral suspension", "topical cream", "topical ointment",
        "transdermal patch", "ophthalmic solution", "inhalation powder", "metered dose inhaler",
        "nasal spray", "oral liquid", "oral elixir", "oral syrup", "rectal suppository",
        "chewable tablet", "delayed release", "extended release", "disintegrating"
    ]
    for b in boilerplate:
        if b in a:
            return False
    # Reject if alias is just canonical name + single generic formulation word
    generic_forms = {
        "injection", "tablet", "capsule", "solution", "suspension",
        "cream", "ointment", "lotion", "powder", "syrup", "patch", "spray", "gel"
    }
    parts = a.split()
    if len(parts) >= 2 and parts[-1] in generic_forms:
        base = " ".join(parts[:-1])
        if base == canonical_name_lower:
            return False
    return True


def insert_drug_nodes(graph, rxcui_to_canonical, rxcui_to_aliases, rxcui_to_unii, rxcui_to_atc):
    """
    Batch-upsert Drug nodes into FalkorDB.
    Each node has:
      rxcui      : str   - primary key
      name       : str   - display name (original casing from RxNorm)
      name_lower : str   - canonical lowercase lookup key  (Rule 1)
      aliases    : list  - clean human-usable alias strings (Rule 2)
      unii       : str   - FDA UNII identifier from MTHSPL (for openFDA joins)
    atc_code   : str   - ATC code when present in RXNSAT
    """
    rows = []
    for rxcui, (display, name_lower) in rxcui_to_canonical.items():
        # Aliases: remove canonical name and filter out noisy dosage strings
        raw_aliases = rxcui_to_aliases.get(rxcui, set())
        clean_aliases = sorted([a for a in raw_aliases if is_clean_alias(a, name_lower)])
        rows.append({
            "rxcui":      rxcui,
            "name":       display,
            "name_lower": name_lower,
            "aliases":    clean_aliases,
            "unii":       rxcui_to_unii.get(rxcui),
            "atc_code":   rxcui_to_atc.get(rxcui),
        })

    total       = len(rows)
    inserted    = 0
    num_batches = math.ceil(total / BATCH_SIZE)
    t0 = time.time()

    # MERGE on rxcui so re-runs are idempotent
    cypher = """
    UNWIND $rows AS row
    MERGE (d:Drug {rxcui: row.rxcui})
    SET d.name       = row.name,
        d.name_lower = row.name_lower,
        d.aliases    = row.aliases,
        d.unii       = row.unii,
        d.atc_code   = coalesce(row.atc_code, d.atc_code)
    """

    for batch_idx in range(num_batches):
        chunk = rows[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
        graph.query(cypher, {"rows": chunk})
        inserted += len(chunk)
        if (batch_idx + 1) % 5 == 0 or batch_idx == num_batches - 1:
            log.info("  Batch %d/%d - %d/%d nodes upserted", batch_idx + 1, num_batches, inserted, total)

    elapsed = time.time() - t0
    log.info("Drug node insertion complete: %d nodes in %.1fs", inserted, elapsed)
    return inserted


# ─────────────────────────────────────────────
# STEP F — Create indexes
# ─────────────────────────────────────────────
def create_indexes(graph):
    log.info("Creating FalkorDB indexes ...")
    indexes = [
        "CREATE INDEX FOR (d:Drug) ON (d.rxcui)",
        "CREATE INDEX FOR (d:Drug) ON (d.name_lower)",
        "CREATE INDEX FOR (d:Drug) ON (d.unii)",
    ]
    for idx in indexes:
        try:
            graph.query(idx)
            log.info("  OK: %s", idx)
        except Exception as e:
            log.warning("  Index may already exist (OK): %s", e)


# ─────────────────────────────────────────────
# RULE 3 — Post-insertion Health Checks
# ─────────────────────────────────────────────
def assert_count_range(graph, cypher, min_val, max_val=None, label=""):
    """Assert query returns a count within [min_val, max_val]. Raises on failure."""
    result = graph.query(cypher)
    c = result.result_set[0][0]
    assert c >= min_val, f"FAIL [{label}]: got {c}, expected >= {min_val}"
    if max_val is not None:
        assert c <= max_val, f"FAIL [{label}]: got {c}, expected <= {max_val}"
    log.info("  PASS  %-45s  %d", label, c)
    return c


def assert_zero(graph, cypher, label=""):
    """Assert query returns 0. Raises on failure."""
    result = graph.query(cypher)
    c = result.result_set[0][0]
    assert c == 0, f"FAIL [{label}]: expected 0, got {c}"
    log.info("  PASS  %-45s  0 (clean)", label)


def run_health_checks(graph, name_to_rxcui: dict):
    log.info("")
    log.info("=" * 60)
    log.info("SESSION 1 HEALTH CHECKS")
    log.info("=" * 60)

    # 1. Total Drug node count
    total = assert_count_range(
        graph,
        "MATCH (d:Drug) RETURN count(d)",
        min_val=5000, max_val=80000,
        label="Drug node count",
    )

    # 2. No Drug node with NULL rxcui
    assert_zero(
        graph,
        "MATCH (d:Drug) WHERE d.rxcui IS NULL RETURN count(d)",
        label="Drug nodes with NULL rxcui",
    )

    # 3. No Drug node with NULL name_lower
    assert_zero(
        graph,
        "MATCH (d:Drug) WHERE d.name_lower IS NULL RETURN count(d)",
        label="Drug nodes with NULL name_lower",
    )

    # 4. No duplicate rxcui values
    assert_zero(
        graph,
        "MATCH (d:Drug) WITH d.rxcui AS r, count(*) AS c WHERE c > 1 RETURN count(r)",
        label="Duplicate rxcui values",
    )

    # 5. No duplicate name_lower values
    assert_zero(
        graph,
        "MATCH (d:Drug) WITH d.name_lower AS n, count(*) AS c WHERE c > 1 RETURN count(n)",
        label="Duplicate name_lower values",
    )

    # 6. Spot-check: known drugs must be resolvable via the lookup map
    #    and their RXCUI must exist in the graph.
    #    We look up by RXCUI (not name_lower) because RxNorm canonical names
    #    may differ from common names (e.g. "rifampin" vs "rifampicin").
    # 6. Spot-check: known drugs must be resolvable via the lookup map
    #    and their RXCUI must exist in the graph.
    known_drugs = [
        "simvastatin", "clarithromycin", "warfarin", "codeine",
        "digoxin", "rifampicin", "sertraline", "metformin",
        "amiodarone", "tramadol", "ondansetron", "zofran", "zocor",
    ]
    log.info("  Spot-checking %d known drugs / brands via RXCUI lookup ...", len(known_drugs))
    for name in known_drugs:
        rxcui = name_to_rxcui.get(name)
        if rxcui is None:
            raise AssertionError(f"FAIL: Known drug/brand '{name}' not in name_to_rxcui map")
        result = graph.query(
            "MATCH (d:Drug {rxcui: $r}) RETURN d.name_lower, d.unii, d.aliases",
            {"r": rxcui},
        )
        if result.result_set:
            canonical, unii, aliases = result.result_set[0]
            log.info(
                "    PASS  %-20s -> rxcui=%-8s  canonical='%-15s'  unii=%-12s  aliases=%d",
                name, rxcui, canonical, str(unii), len(aliases) if aliases else 0,
            )
        else:
            raise AssertionError(f"FAIL: rxcui={rxcui} for '{name}' NOT found in FalkorDB graph")

    # Specific check: Ondansetron must have non-null UNII and Zofran in aliases
    res_ond = graph.query("MATCH (d:Drug {name_lower: 'ondansetron'}) RETURN d.unii, d.aliases")
    assert res_ond.result_set, "FAIL: ondansetron not found in graph"
    ond_unii, ond_aliases = res_ond.result_set[0]
    assert ond_unii, "FAIL: ondansetron has NULL unii"
    assert ond_aliases and "zofran" in ond_aliases, f"FAIL: 'zofran' not in ondansetron aliases ({ond_aliases})"
    log.info("  PASS  %-45s  unii='%s', %d aliases (includes 'zofran')", "Ondansetron properties", ond_unii, len(ond_aliases))

    # Specific check: Prednisone aliases must be clean human names (no dosage strings)
    res_pred = graph.query("MATCH (d:Drug {name_lower: 'prednisone'}) RETURN d.aliases")
    assert res_pred.result_set, "FAIL: prednisone not found in graph"
    pred_aliases = res_pred.result_set[0][0]
    import re
    has_digits = any(re.search(r"\d", a) for a in pred_aliases)
    assert not has_digits, f"FAIL: Prednisone still has noisy dose strings: {pred_aliases}"
    log.info("  PASS  %-45s  clean human names (%s)", "Prednisone aliases", pred_aliases)

    # 7. Total Drug nodes with UNII
    res_unii = graph.query("MATCH (d:Drug) WHERE d.unii IS NOT NULL RETURN count(d)")
    c_unii = res_unii.result_set[0][0]
    assert c_unii >= 4000, f"FAIL: Expected >= 4000 drugs with UNII, got {c_unii}"
    log.info("  PASS  %-45s  %d", "Drug nodes with non-null UNII", c_unii)

    atc_count = graph.query(
        "MATCH (d:Drug) WHERE d.atc_code IS NOT NULL RETURN count(d)"
    ).result_set[0][0]
    if atc_count:
        log.info("  PASS  %-45s  %d", "Drug nodes with ATC code", atc_count)
    else:
        log.warning("  WARN  No ATC codes populated; none are present in the local RXNSAT snapshot")

    # 8. name_to_rxcui.json was written and is non-trivial (no dose-strength alias keys)
    assert os.path.exists(LOOKUP_MAP_FILE), f"FAIL: {LOOKUP_MAP_FILE} not found on disk"
    with open(LOOKUP_MAP_FILE, encoding="utf-8") as fh:
        m = json.load(fh)
    assert len(m) >= 5000, f"FAIL: name_to_rxcui map too small: {len(m)} entries"
    pred_rxcui = m.get("prednisone")
    noisy_pred = [k for k in m if k.startswith("prednisone ") and pred_rxcui and m[k] == pred_rxcui]
    assert not noisy_pred, f"FAIL: name_to_rxcui still has noisy prednisone aliases: {noisy_pred[:10]}"
    log.info("  PASS  %-45s  %d entries", LOOKUP_MAP_FILE, len(m))
    assert os.path.exists(RXCUI_INGREDIENT_MAP_FILE), (
        f"FAIL: {RXCUI_INGREDIENT_MAP_FILE} not found"
    )
    with open(RXCUI_INGREDIENT_MAP_FILE, encoding="utf-8") as fh:
        rxcui_to_ingredient = json.load(fh)
    assert all(rxcui in rxcui_to_ingredient for rxcui in name_to_rxcui.values()), (
        "FAIL: RXCUI-to-ingredient map is missing canonical Drug RXCUIs"
    )
    log.info(
        "  PASS  %-45s  %d entries",
        RXCUI_INGREDIENT_MAP_FILE,
        len(rxcui_to_ingredient),
    )

    # 9. Graph integrity: only Drug nodes should exist at this point
    result = graph.query("CALL db.labels() YIELD label RETURN label")
    labels_in_graph = [row[0] for row in result.result_set]
    expected_labels = {
        "Drug", "Enzyme", "Transporter", "Condition", "DrugClass",
        "Patient", "Prescription", "Variant", "Phenotype",
    }
    unexpected = [label for label in labels_in_graph if label not in expected_labels]
    if unexpected:
        log.warning("  WARN  Unknown node labels in graph: %s", unexpected)
    else:
        log.info("  PASS  %-45s  %s", "Recognized graph labels", sorted(labels_in_graph))

    log.info("=" * 60)
    log.info("ALL SESSION 1 HEALTH CHECKS PASSED [SUCCESS] (%d Drug nodes)", total)
    log.info("=" * 60)
    log.info("")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    session_start = time.time()
    log.info("=" * 60)
    log.info("SESSION 1 -- RxNorm Drug Node Spine")
    log.info("=" * 60)

    # Connect to FalkorDB
    log.info("Connecting to FalkorDB at %s:%d ...", FALKORDB_HOST, FALKORDB_PORT)
    try:
        db    = FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT)
        graph = db.select_graph(GRAPH_NAME)
        graph.query("RETURN 1")
        log.info("Connected to FalkorDB graph '%s'", GRAPH_NAME)
    except Exception as e:
        log.error("Cannot connect to FalkorDB: %s", e)
        raise

    # Step A: Parse RXNCONSO
    log.info("")
    log.info("Step A: Parsing RXNCONSO.RRF (%s) ...", RXNCONSO_FILE)
    rxcui_to_canonical, rxcui_to_aliases, rxcui_to_unii = parse_rxnconso(RXNCONSO_FILE)
    log.info(
        "  Ingredient nodes: %d | Alias-carrying RXCUIs: %d | UNIIs: %d",
        len(rxcui_to_canonical), len(rxcui_to_aliases), len(rxcui_to_unii),
    )
    rxcui_to_atc = parse_rxnsat_atc(RXNSAT_FILE, set(rxcui_to_canonical))

    # Step B: Parse RXNREL for child->IN mapping
    log.info("")
    log.info("Step B: Parsing RXNREL.RRF for child->ingredient mappings ...")
    child_to_in = parse_rxnrel_child_to_in(RXNREL_FILE, set(rxcui_to_canonical.keys()))
    rxcui_to_ingredient = {rxcui: rxcui for rxcui in rxcui_to_canonical}
    rxcui_to_ingredient.update(child_to_in)
    with open(RXCUI_INGREDIENT_MAP_FILE, "w", encoding="utf-8") as fh:
        json.dump(rxcui_to_ingredient, fh)
    log.info(
        "Saved %s (%d RXCUI mappings)",
        RXCUI_INGREDIENT_MAP_FILE,
        len(rxcui_to_ingredient),
    )

    # Step C: Merge cross-RXCUI aliases
    log.info("")
    log.info("Step C: Merging alias names into parent ingredient nodes ...")
    merge_aliases(rxcui_to_canonical, rxcui_to_aliases, child_to_in, rxcui_to_unii)

    # Step D: Build and save lookup map
    log.info("")
    log.info("Step D: Building name_to_rxcui lookup map ...")
    name_to_rxcui = build_lookup_map(rxcui_to_canonical, rxcui_to_aliases)
    if os.path.exists(LOOKUP_MAP_FILE):
        with open(LOOKUP_MAP_FILE, encoding="utf-8") as fh:
            previous_map = json.load(fh)
        preserved = 0
        canonical_rxcuis = set(rxcui_to_canonical)
        for name, rxcui in previous_map.items():
            normalized_name = normalize_name(name)
            if rxcui in canonical_rxcuis and normalized_name not in name_to_rxcui:
                name_to_rxcui[normalized_name] = rxcui
                preserved += 1
        if preserved:
            log.info("Preserved %d verified names from the previous lookup map", preserved)
    with open(LOOKUP_MAP_FILE, "w", encoding="utf-8") as fh:
        json.dump(name_to_rxcui, fh, ensure_ascii=False)
    log.info("Saved %s  (%d entries)", LOOKUP_MAP_FILE, len(name_to_rxcui))

    # Step E: Create indexes
    log.info("")
    log.info("Step E: Creating FalkorDB indexes ...")
    create_indexes(graph)

    # Step F: Insert Drug nodes
    log.info("")
    log.info("Step F: Inserting Drug nodes into FalkorDB ...")
    insert_drug_nodes(
        graph, rxcui_to_canonical, rxcui_to_aliases, rxcui_to_unii, rxcui_to_atc
    )

    # Rule 3: Health checks
    log.info("")
    run_health_checks(graph, name_to_rxcui)

    elapsed = time.time() - session_start
    log.info("SESSION 1 COMPLETE in %.1fs", elapsed)
    log.info("Next step: Run session2_ddinter.py")


if __name__ == "__main__":
    main()
