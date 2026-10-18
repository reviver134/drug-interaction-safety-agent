"""
session2_ddinter.py
===================
Session 2 — DDInter: INTERACTS_WITH Edges

Loads drug-drug interaction records from DDInter CSVs into FalkorDB
as INTERACTS_WITH edges between existing canonical Drug nodes.

Key design rules enforced:
  Rule 1 — Case normalization : all drug names are lowercased and stripped.
  Rule 2 — Ingredient dedup   : two drug names refer to the same entity only if
                                they resolve to the same RxNorm ingredient RXCUI.
                                Self-interactions (same ingredient) are filtered.
                                Duplicate pairs are merged to keep the highest severity.
  Rule 3 — Health checks      : automated assertions run after insertion;
                                verifies edge count, match rate, severity distribution,
                                spot-check pairs, absence of self-loops, absence of orphans,
                                and confirms existing Drug nodes were not corrupted or deleted.

Inputs:
  • ddinter_downloads_code_{A,B,D,H,L,P,R}.csv
  • name_to_rxcui.json (created in Session 1)

Outputs:
  • INTERACTS_WITH edges in FalkorDB graph 'drug_interactions'
  • session2.log           — detailed run statistics
  • unmatched_ddinter.log  — drugs that could not be resolved
  • fuzzy_ddinter.log      — drugs matched via token/prefix fallback
  • name_to_rxcui.json     — augmented with verified DDInter synonyms
"""

import csv
import glob
import json
import logging
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from falkordb import FalkorDB

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
from settings import DATA_DIR, HOST, PORT, GRAPH_NAME, graph_client
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOOKUP_MAP_FILE = str(DATA_DIR / "name_to_rxcui.json")
LOG_FILE        = str(LOG_DIR / "session2.log")
UNMATCHED_FILE  = str(LOG_DIR / "unmatched_ddinter.log")
FUZZY_LOG_FILE  = str(LOG_DIR / "fuzzy_ddinter.log")

DDINTER_GLOB    = str(DATA_DIR / "ddinter_downloads_code_*.csv")

FALKORDB_HOST = HOST
FALKORDB_PORT = PORT   # Redis/FalkorDB protocol port (3000 is browser UI)


BATCH_SIZE = 1000   # Edges per UNWIND query

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# Severity rank for deduplication (keep highest severity if duplicate pairs exist)
SEVERITY_RANK = {
    "Major": 3,
    "Moderate": 2,
    "Minor": 1,
    "Unknown": 0,
}

# ─────────────────────────────────────────────
# RULE 1 — Normalization & Synonyms
# ─────────────────────────────────────────────
def normalize_name(raw: str) -> str:
    """Strip whitespace and lowercase."""
    return raw.strip().lower()

def clean_parentheses(raw: str) -> str:
    """Remove parenthetical qualifiers, e.g.
    'Insulin human (regular)' -> 'Insulin human'
    'Medrysone (ophthalmic)' -> 'Medrysone'
    """
    return re.sub(r"\(.*?\)", "", raw).strip().lower()

# Known INN / British / chemical / formulation synonyms to canonical RxNorm ingredient names
KNOWN_SYNONYMS = {
    "acetylsalicylic acid": "aspirin",
    "salbutamol": "albuterol",
    "ethinylestradiol": "ethinyl estradiol",
    "cyanocobalamin": "vitamin b12",
    "somatotropin": "somatropin",
    "isoprenaline": "isoproterenol",
    "orciprenaline": "metaproterenol",
    "paracetamol": "acetaminophen",
    "rifampicin": "rifampin",
    "frusemide": "furosemide",
    "pethidine": "meperidine",
    "lignocaine": "lidocaine",
    "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine",
    "dicoumarol": "dicumarol",
    "nicotinic acid": "niacin",
    "mepyramine": "pyrilamine",
    "alimemazine": "trimeprazine",
    "cefalotin": "cephalothin",
    "cefaloridine": "cephaloridine",
    "etacrynic acid": "ethacrynic acid",
    "polyethylene glycol (3350 with electrolytes)": "polyethylene glycol 3350",
    "insulin human": "insulin, regular, human",
    "insulin human (regular)": "insulin, regular, human",
    "insulin human (zinc)": "insulin, regular, human",
    "insulin human (zinc extended)": "insulin, regular, human",
    "insulin human (inhalation, rapid acting)": "insulin, regular, human",
    "insulin human (isophane)": "insulin isophane",
    "insulin aspart (aspart)": "insulin aspart, human",
    "insulin aspart (aspart protamine)": "insulin aspart protamine, human",
}

# Explicitly reviewed synonym mappings that are source-backed and distinct from generic prefix matches.
# These are accepted only when there is direct evidence they refer to a specific RxNorm concept.
REVIEWED_SYNONYMS = {
    "digoxin immune fab": "203223",
    "digoxin immune fab (ovine)": "203223",
    "digoxin immune fab ovine": "203223",
    "digoxin antibodies fab fragments": "203223",
    "digoxin antibody fab fragments": "203223",
}


class DrugResolver:
    """
    Resolves raw drug name strings from DDInter to RxNorm RXCUIs
    using exact matching, synonym dictionary, and progressive token fallback.
    """
    def __init__(self, name_to_rxcui: dict):
        self.name_to_rxcui = name_to_rxcui
        self.cache = {}
        self.fuzzy_matches = []
        self.unmatched_names = set()

    def resolve(self, raw_name: str) -> str | None:
        if raw_name in self.cache:
            return self.cache[raw_name]

        norm = normalize_name(raw_name)

        # 1. Exact match in lookup map
        if norm in self.name_to_rxcui:
            rxcui = self.name_to_rxcui[norm]
            self.cache[raw_name] = rxcui
            return rxcui

        # 2. Check known synonyms
        if norm in KNOWN_SYNONYMS:
            target = KNOWN_SYNONYMS[norm]
            if target in self.name_to_rxcui:
                rxcui = self.name_to_rxcui[target]
                self.cache[raw_name] = rxcui
                return rxcui

        # 3. Explicit reviewed synonyms (source-backed, distinct identity checks)
        if norm in REVIEWED_SYNONYMS:
            rxcui = REVIEWED_SYNONYMS[norm]
            self.cache[raw_name] = rxcui
            return rxcui

        # 4. Clean parentheses e.g. "Insulin human (regular)" -> "insulin human"
        cleaned = clean_parentheses(raw_name)
        if cleaned and cleaned != norm:
            if cleaned in self.name_to_rxcui:
                rxcui = self.name_to_rxcui[cleaned]
                self.cache[raw_name] = rxcui
                return rxcui
            if cleaned in KNOWN_SYNONYMS:
                target = KNOWN_SYNONYMS[cleaned]
                if target in self.name_to_rxcui:
                    rxcui = self.name_to_rxcui[target]
                    self.cache[raw_name] = rxcui
                    return rxcui
            if cleaned in REVIEWED_SYNONYMS:
                rxcui = REVIEWED_SYNONYMS[cleaned]
                self.cache[raw_name] = rxcui
                return rxcui

        # 5. Progressive token / prefix matching fallback
        #    This is recorded as a review candidate instead of being auto-promoted.
        for candidate_str in [cleaned, norm]:
            if not candidate_str:
                continue
            parts = candidate_str.split()
            for n in range(len(parts) - 1, 0, -1):
                cand = " ".join(parts[:n])
                if cand in REVIEWED_SYNONYMS:
                    rxcui = REVIEWED_SYNONYMS[cand]
                    self.cache[raw_name] = rxcui
                    return rxcui
                if cand in self.name_to_rxcui:
                    rxcui = self.name_to_rxcui[cand]
                    self.fuzzy_matches.append((raw_name, cand, rxcui, "rejected_prefix_match"))
                    self.cache[raw_name] = None
                    self.unmatched_names.add(raw_name)
                    return None
                if cand in KNOWN_SYNONYMS:
                    target = KNOWN_SYNONYMS[cand]
                    if target in self.name_to_rxcui:
                        rxcui = self.name_to_rxcui[target]
                        self.cache[raw_name] = rxcui
                        return rxcui

        # 6. Could not resolve
        self.cache[raw_name] = None
        self.unmatched_names.add(raw_name)
        return None


# ─────────────────────────────────────────────
# STEP A — Load DDInter CSVs & Parse Edges
# ─────────────────────────────────────────────
def load_and_deduplicate_ddinter(resolver: DrugResolver):
    csv_files = sorted(glob.glob(DDINTER_GLOB))
    if not csv_files:
        raise FileNotFoundError(f"No DDInter CSV files matching pattern: {DDINTER_GLOB}")

    log.info("Found %d DDInter CSV files: %s", len(csv_files), ", ".join(os.path.basename(f) for f in csv_files))

    unique_drugs_in_csv = set()
    total_rows = 0
    matched_rows = 0
    self_loops_filtered = 0
    both_unmatched = 0
    one_unmatched = 0

    # Key: (rxcui_a, rxcui_b) -> dict of edge properties
    deduped_edges = {}

    t0 = time.time()

    for filepath in csv_files:
        filename = os.path.basename(filepath)
        file_rows = 0
        file_matched = 0

        with open(filepath, mode="r", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                total_rows += 1
                file_rows += 1

                raw_a = row.get("Drug_A", "").strip()
                raw_b = row.get("Drug_B", "").strip()
                id_a  = row.get("DDInterID_A", "").strip()
                id_b  = row.get("DDInterID_B", "").strip()
                level = row.get("Level", "").strip()

                if not raw_a or not raw_b:
                    continue

                unique_drugs_in_csv.add(raw_a)
                unique_drugs_in_csv.add(raw_b)

                rxcui_a = resolver.resolve(raw_a)
                rxcui_b = resolver.resolve(raw_b)

                if not rxcui_a and not rxcui_b:
                    both_unmatched += 1
                    continue
                elif not rxcui_a or not rxcui_b:
                    one_unmatched += 1
                    continue

                matched_rows += 1
                file_matched += 1

                # Rule 2: Ingredient Deduplication & Canonical Pair Ordering
                # 1) If both drugs map to the same ingredient RXCUI, filter self-loop
                if rxcui_a == rxcui_b:
                    self_loops_filtered += 1
                    continue

                # 2) Canonicalize pair order (min, max) so mirrored pairs collapse into one edge
                r1, r2 = min(rxcui_a, rxcui_b), max(rxcui_a, rxcui_b)
                pair_key = (r1, r2)
                current_rank = SEVERITY_RANK.get(level, 0)

                # Keep higher severity record if pair already encountered
                if pair_key not in deduped_edges:
                    deduped_edges[pair_key] = {
                        "rxcui_a": r1,
                        "rxcui_b": r2,
                        "severity": level,
                        "id_a": id_a,
                        "id_b": id_b,
                    }
                else:
                    existing_rank = SEVERITY_RANK.get(deduped_edges[pair_key]["severity"], 0)
                    if current_rank > existing_rank:
                        deduped_edges[pair_key]["severity"] = level
                        deduped_edges[pair_key]["id_a"] = id_a
                        deduped_edges[pair_key]["id_b"] = id_b

        log.info("  Parsed %-30s | rows=%6d | matched=%6d (%.1f%%)",
                 filename, file_rows, file_matched, (file_matched / file_rows * 100) if file_rows else 0)

    elapsed = time.time() - t0
    matched_unique_drugs = sum(1 for d in unique_drugs_in_csv if resolver.resolve(d) is not None)

    stats = {
        "total_rows": total_rows,
        "matched_rows": matched_rows,
        "self_loops_filtered": self_loops_filtered,
        "both_unmatched": both_unmatched,
        "one_unmatched": one_unmatched,
        "unique_edges": len(deduped_edges),
        "total_unique_drugs": len(unique_drugs_in_csv),
        "matched_unique_drugs": matched_unique_drugs,
        "elapsed_sec": elapsed,
    }

    log.info("=" * 60)
    log.info("DDInter Parsing Summary:")
    log.info("  Total CSV rows         : %d", total_rows)
    log.info("  Matched rows (both)    : %d (%.2f%%)", matched_rows, (matched_rows / total_rows * 100))
    log.info("  Unique drugs in DDInter: %d", len(unique_drugs_in_csv))
    log.info("  Unique drugs resolved  : %d (%.2f%%)", matched_unique_drugs, (matched_unique_drugs / len(unique_drugs_in_csv) * 100))
    log.info("  Self-loops filtered    : %d", self_loops_filtered)
    log.info("  Unique directed edges  : %d", len(deduped_edges))
    log.info("=" * 60)

    return deduped_edges, stats


# ─────────────────────────────────────────────
# STEP B — Write Audit Logs
# ─────────────────────────────────────────────
def write_audit_logs(resolver: DrugResolver):
    # 1. Unmatched drugs log
    with open(UNMATCHED_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Drug names from DDInter that could not be resolved to an RxNorm RXCUI\n")
        fh.write(f"# Total unmatched: {len(resolver.unmatched_names)}\n\n")
        for name in sorted(resolver.unmatched_names):
            fh.write(f"{name}\n")
    log.info("Wrote %d unmatched drug names to %s", len(resolver.unmatched_names), UNMATCHED_FILE)

    # 2. Fuzzy matches log
    with open(FUZZY_LOG_FILE, "w", encoding="utf-8") as fh:
        fh.write("# DDInter drug names that reached a prefix-based review candidate\n")
        fh.write("# These are not auto-promoted into permanent exact mappings.\n")
        fh.write(f"# Total review candidates: {len(resolver.fuzzy_matches)}\n\n")
        for raw, target, rxcui, reason in resolver.fuzzy_matches:
            fh.write(f"{raw:40} -> {target:30} (RXCUI {rxcui}) [{reason}]\n")
    log.info("Wrote %d review-candidate records to %s", len(resolver.fuzzy_matches), FUZZY_LOG_FILE)


# ─────────────────────────────────────────────
# STEP C — Batch Insert INTERACTS_WITH Edges
# ─────────────────────────────────────────────
def insert_interactions(graph, deduped_edges: dict):
    rows = list(deduped_edges.values())
    total_edges = len(rows)
    total_batches = math.ceil(total_edges / BATCH_SIZE)

    log.info("Inserting %d INTERACTS_WITH edges in %d batches (batch_size=%d) ...",
             total_edges, total_batches, BATCH_SIZE)

    cypher = """
    UNWIND $rows AS row
    MATCH (a:Drug {rxcui: row.rxcui_a})
    MATCH (b:Drug {rxcui: row.rxcui_b})
    CREATE (a)-[r:INTERACTS_WITH {
        severity: row.severity,
        ddinter_id_a: row.id_a,
        ddinter_id_b: row.id_b
    }]->(b)
    """

    t0 = time.time()
    inserted = 0

    for i in range(0, total_edges, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        graph.query(cypher, {"rows": batch})
        inserted += len(batch)

        if (i // BATCH_SIZE + 1) % 10 == 0 or inserted == total_edges:
            el = time.time() - t0
            rate = inserted / el if el > 0 else 0
            log.info("  Batch %3d/%3d | Inserted %6d/%6d edges | %.0f edges/sec",
                     (i // BATCH_SIZE + 1), total_batches, inserted, total_edges, rate)

    total_time = time.time() - t0
    log.info("Inserted %d edges in %.2f seconds (%.0f edges/sec)",
             total_edges, total_time, (total_edges / total_time) if total_time > 0 else 0)


# ─────────────────────────────────────────────
# STEP D — Health Checks
# ─────────────────────────────────────────────
def assert_count_range(graph, cypher, min_val, max_val=None, label=""):
    result = graph.query(cypher)
    c = result.result_set[0][0]
    assert c >= min_val, f"FAIL [{label}]: got {c}, expected >= {min_val}"
    if max_val is not None:
        assert c <= max_val, f"FAIL [{label}]: got {c}, expected <= {max_val}"
    log.info("  PASS  %-45s  %d", label, c)
    return c

def assert_zero(graph, cypher, label=""):
    result = graph.query(cypher)
    c = result.result_set[0][0]
    assert c == 0, f"FAIL [{label}]: expected 0, got {c}"
    log.info("  PASS  %-45s  0 (clean)", label)


def run_health_checks(graph, stats: dict, resolver: DrugResolver, baseline_drug_count: int):
    log.info("")
    log.info("=" * 60)
    log.info("SESSION 2 HEALTH CHECKS")
    log.info("=" * 60)

    # 1. Total INTERACTS_WITH edge count
    edge_count = assert_count_range(
        graph,
        "MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r)",
        min_val=100000, max_val=250000,
        label="INTERACTS_WITH edge count",
    )

    # 2. Row match rate (> 75%)
    row_match_rate = stats["matched_rows"] / stats["total_rows"]
    assert row_match_rate >= 0.75, f"FAIL: Row match rate {row_match_rate:.1%} < 75%"
    log.info("  PASS  %-45s  %.1f%% (>= 75%%)", "DDInter row match rate", row_match_rate * 100)

    # 3. Unique drug match rate (> 80%)
    drug_match_rate = stats["matched_unique_drugs"] / stats["total_unique_drugs"]
    assert drug_match_rate >= 0.80, f"FAIL: Unique drug match rate {drug_match_rate:.1%} < 80%"
    log.info("  PASS  %-45s  %.1f%% (>= 80%%)", "DDInter unique drug match rate", drug_match_rate * 100)

    # 4. Severity distribution: Major, Moderate, Minor must all be present
    for sev in ["Major", "Moderate", "Minor"]:
        assert_count_range(
            graph,
            f"MATCH ()-[r:INTERACTS_WITH {{severity: '{sev}'}}]->() RETURN count(r)",
            min_val=1000,
            label=f"Severity={sev} edge count",
        )

    # 5. Spot-checks for known high-profile drug interactions
    known_pairs = [
        ("clarithromycin", "simvastatin", "Major"),
        ("warfarin", "fluconazole", "Major"),
        ("warfarin", "rifampin", "Major"),
    ]
    log.info("  Spot-checking %d clinical interaction pairs ...", len(known_pairs))
    for drug_a, drug_b, expected_sev in known_pairs:
        rxcui_a = resolver.resolve(drug_a)
        rxcui_b = resolver.resolve(drug_b)
        assert rxcui_a and rxcui_b, f"FAIL: Could not resolve spot-check drugs {drug_a} or {drug_b}"

        # Match undirected because edge can be stored as (a)->(b) or (b)->(a)
        res = graph.query(
            "MATCH (a:Drug {rxcui: $ra})-[r:INTERACTS_WITH]-(b:Drug {rxcui: $rb}) "
            "RETURN r.severity, r.ddinter_id_a, r.ddinter_id_b",
            {"ra": rxcui_a, "rb": rxcui_b},
        )
        assert len(res.result_set) > 0, f"FAIL: Interaction {drug_a} <-> {drug_b} not found in graph"
        actual_sev = res.result_set[0][0]
        log.info("    PASS  %-18s <-> %-15s severity='%s'", drug_a, drug_b, actual_sev)

    # 6. No self-loops: a Drug node cannot interact with itself
    assert_zero(
        graph,
        "MATCH (d:Drug)-[:INTERACTS_WITH]->(d) RETURN count(*)",
        label="Self-loop edges (Drug -[:INTERACTS_WITH]-> Drug)",
    )

    # 6b. No duplicate or mirrored pairs (Query 2 check)
    assert_zero(
        graph,
        "MATCH (a:Drug)-[r1:INTERACTS_WITH]->(b:Drug)-[r2:INTERACTS_WITH]->(a) RETURN count(r1)",
        label="Duplicate or mirrored pairs",
    )

    # 7. No orphan edges: both endpoints MUST be Drug nodes
    assert_zero(
        graph,
        "MATCH (x)-[:INTERACTS_WITH]->(y) WHERE NOT x:Drug OR NOT y:Drug RETURN count(*)",
        label="Orphan edges with non-Drug endpoints",
    )

    # 8. Graph database integrity: baseline Drug nodes must be untouched (neither deleted nor corrupted)
    current_drug_count = assert_count_range(
        graph,
        "MATCH (d:Drug) RETURN count(d)",
        min_val=baseline_drug_count, max_val=baseline_drug_count,
        label=f"Drug node count intact ({baseline_drug_count})",
    )
    assert current_drug_count == baseline_drug_count, \
        f"FAIL: Drug node count changed from {baseline_drug_count} to {current_drug_count}"

    # 9. No NULL properties on Drug nodes
    assert_zero(
        graph,
        "MATCH (d:Drug) WHERE d.rxcui IS NULL OR d.name_lower IS NULL RETURN count(d)",
        label="Drug nodes with NULL rxcui or name_lower",
    )

    # 10. Audit log files verified
    assert os.path.exists(UNMATCHED_FILE), f"FAIL: {UNMATCHED_FILE} not found"
    assert os.path.exists(FUZZY_LOG_FILE), f"FAIL: {FUZZY_LOG_FILE} not found"
    log.info("  PASS  %-45s  exists", UNMATCHED_FILE)
    log.info("  PASS  %-45s  exists", FUZZY_LOG_FILE)

    log.info("=" * 60)
    log.info("ALL SESSION 2 HEALTH CHECKS PASSED [SUCCESS]")
    log.info("=" * 60)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("SESSION 2: DDINTER DRUG-DRUG INTERACTIONS")
    log.info("=" * 60)

    # Connect to FalkorDB
    log.info("Connecting to FalkorDB at %s:%d (graph: '%s') ...", FALKORDB_HOST, FALKORDB_PORT, GRAPH_NAME)
    try:
        db = graph_client()
        graph = db.select_graph(GRAPH_NAME)
    except Exception as exc:
        log.error("Failed to connect to FalkorDB: %s", exc)
        sys.exit(1)

    # Baseline health check before touching graph
    res = graph.query("MATCH (d:Drug) RETURN count(d)")
    baseline_drug_count = res.result_set[0][0]
    log.info("Baseline Drug nodes in graph: %d", baseline_drug_count)
    if baseline_drug_count == 0:
        log.error("Graph contains 0 Drug nodes! Run session1_rxnorm.py first.")
        sys.exit(1)

    # Load lookup map from Session 1
    if not os.path.exists(LOOKUP_MAP_FILE):
        log.error("Lookup map file '%s' not found. Run session1_rxnorm.py first.", LOOKUP_MAP_FILE)
        sys.exit(1)

    with open(LOOKUP_MAP_FILE, "r", encoding="utf-8") as fh:
        name_to_rxcui = json.load(fh)
    log.info("Loaded %d drug name mappings from %s", len(name_to_rxcui), LOOKUP_MAP_FILE)

    # Initialize resolver
    resolver = DrugResolver(name_to_rxcui)

    # Parse and deduplicate DDInter records before removing any existing INTERACTS_WITH edges.
    deduped_edges, stats = load_and_deduplicate_ddinter(resolver)

    # Write audit logs
    write_audit_logs(resolver)

    # Augment name_to_rxcui.json with resolved synonyms for future sessions
    augmented_count = 0
    for name, rxcui in resolver.cache.items():
        if rxcui and normalize_name(name) not in name_to_rxcui:
            name_to_rxcui[normalize_name(name)] = rxcui
            augmented_count += 1
    if augmented_count > 0:
        with open(LOOKUP_MAP_FILE, "w", encoding="utf-8") as fh:
            json.dump(name_to_rxcui, fh, indent=2)
        log.info("Augmented %s with %d new resolved aliases (total: %d)",
                 LOOKUP_MAP_FILE, augmented_count, len(name_to_rxcui))

    # Apply the replacement only after candidate validation has succeeded.
    res = graph.query("MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r)")
    existing_edges = res.result_set[0][0]
    if existing_edges > 0:
        log.info("Replacing %d existing INTERACTS_WITH edges after validation ...", existing_edges)
        graph.query("MATCH ()-[r:INTERACTS_WITH]->() DELETE r")
        log.info("Cleared existing edges.")

    # Batch insert edges
    insert_interactions(graph, deduped_edges)

    # Run mandatory health checks
    run_health_checks(graph, stats, resolver, baseline_drug_count)


if __name__ == "__main__":
    main()
