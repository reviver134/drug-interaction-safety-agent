"""
session3_fda_cyp.py
===================
Session 3 — FDA CYP / Transporter enzyme layer

Loads Drug → Enzyme / Transporter edges from the FDA table (Table 1):
  INHIBITS, INDUCES, SUBSTRATE_OF with strength where applicable.

This layer enables multi-hop paths such as:
  Clarithromycin -[:INHIBITS]-> CYP3A4 <-[:SUBSTRATE_OF]- Simvastatin

Input:
  • fda_table1_raw.csv  (paste from FDA web table; see project.md)
  • name_to_rxcui.json  (Session 1)

Outputs:
  • Enzyme and Transporter nodes + edges in FalkorDB graph 'drug_interactions'
  • session3.log
  • unmatched_fda.log
"""

import csv
import json
import logging
import math
import os
import re
import sys
import time
from collections import defaultdict

from falkordb import FalkorDB

from session2_ddinter import DrugResolver, normalize_name

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
from settings import DATA_DIR, HOST, PORT, GRAPH_NAME
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

FDA_CSV         = str(DATA_DIR / "fda_table1_raw.csv")
LOOKUP_MAP_FILE = str(DATA_DIR / "name_to_rxcui.json")
LOG_FILE        = str(LOG_DIR / "session3.log")
UNMATCHED_FILE  = str(LOG_DIR / "unmatched_fda.log")

FALKORDB_HOST = HOST
FALKORDB_PORT = PORT

BATCH_SIZE    = 500

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

# Column header (after skipping FDA title row) → (relation, default strength, target kind)
COLUMN_SPECS = {
    "CYP Strg INH":       ("INHIBITS", "strong", "enzyme"),
    "CYP Mod INH":        ("INHIBITS", "moderate", "enzyme"),
    "CYP WK INH":         ("INHIBITS", "weak", "enzyme"),
    "CYP Strg IND":       ("INDUCES", "strong", "enzyme"),
    "CYP Mod IND":        ("INDUCES", "moderate", "enzyme"),
    "CYP WK IND":         ("INDUCES", "weak", "enzyme"),
    "CYP SENS SUB":       ("SUBSTRATE_OF", "sensitive", "enzyme"),
    "CYP Mod SENS SUB":   ("SUBSTRATE_OF", "moderate_sensitive", "enzyme"),
    "TRNSP INH":          ("INHIBITS", None, "transporter"),
    "TRNSP SUB":          ("SUBSTRATE_OF", None, "transporter"),
}

STRENGTH_RANK = {
    "strong": 4,
    "moderate": 3,
    "weak": 2,
    "sensitive": 4,
    "moderate_sensitive": 3,
}

# Longest-first so 2C19 is not truncated to 2C1, 3A4 not to 3A.
CYP_ISOFORMS = [
    "2C19", "3A4", "3A5", "1A2", "2B6", "2C8", "2C9", "2D6",
    "2E1", "2A6", "2C18", "3A",
]
CYP_TOKEN = re.compile(
    r"(?:CYP\s*)?(" + "|".join(CYP_ISOFORMS) + r")(?:\d+)?",
    re.I,
)
# FDA Table 1 writes the 3A family as "3A"; the graph uses CYP3A4 (clinical isoform).
CYP_CANONICAL = {"CYP3A": "CYP3A4"}

# Known transporter symbols in the FDA table (order: longest first for greedy match)
TRANSPORTER_SYMBOLS = sorted(
    [
        "MATE2-K", "OATP1B1", "OATP1B3", "P-gp", "BCRP",
        "OAT1", "OAT3", "OCT2", "MATE1",
    ],
    key=len,
    reverse=True,
)

# Strip FDA footnote digits from substance names, e.g. warfarin19 → warfarin
FOOTNOTE_SUFFIX = re.compile(r"\d+$")


def clean_fda_drug_name(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"[\u00a0\u200b]", " ", s)  # nbsp / zero-width
    s = FOOTNOTE_SUFFIX.sub("", s.strip())
    return normalize_name(s)


def normalize_cyp_symbol(token: str) -> str | None:
    m = CYP_TOKEN.search(token)
    if not m:
        return None
    raw = f"CYP{m.group(1).upper()}"
    return CYP_CANONICAL.get(raw, raw)


def infer_strength(text: str, default: str | None) -> str | None:
    if not text:
        return default
    t = text.lower()
    if "moderately sensitive" in t or "moderate sensitive" in t:
        return "moderate_sensitive"
    if "moderate" in t:
        return "moderate"
    if "strong" in t:
        return "strong"
    if "weak" in t:
        return "weak"
    if "sensitive" in t:
        return "sensitive"
    return default


TRANSPORTER_EXPAND = {
    "OATP1B": ["OATP1B1", "OATP1B3"],  # FDA sometimes names the family, not isoform
}


def parse_transporters(chunk: str) -> list[str]:
    found = []
    remaining = chunk
    for sym in TRANSPORTER_SYMBOLS:
        pattern = re.compile(re.escape(sym), re.I)
        if pattern.search(remaining):
            found.append(sym)
            remaining = pattern.sub(" ", remaining, count=1)
    leftover = remaining.strip()
    if not found:
        m = re.match(r"^([A-Za-z0-9-]+)", leftover)
        if m:
            found.append(m.group(1))
    expanded = []
    for sym in found:
        expanded.extend(TRANSPORTER_EXPAND.get(sym, [sym]))
    return expanded


def parse_cell(cell: str, rel_type: str, default_strength: str | None, kind: str) -> list[tuple[str, str, str | None]]:
    """
    Returns list of (symbol, rel_type, strength) for one table cell.

    FDA cells often list several enzymes then one strength phrase, e.g.
    "2B6; 2C9;  weak inhibitor" — strength applies to every enzyme in the cell.
    """
    if not cell or not str(cell).strip():
        return []

    text = re.sub(r"[\u00a0\u200b]", " ", str(cell)).strip()
    if not text or text in {"\u00a0", " "}:
        return []

    strength = infer_strength(text, default_strength)
    out = []
    if kind == "enzyme":
        enzymes = []
        for m in CYP_TOKEN.finditer(text):
            raw = f"CYP{m.group(1).upper()}"
            sym = CYP_CANONICAL.get(raw, raw)
            if sym not in enzymes:
                enzymes.append(sym)
        for sym in enzymes:
            out.append((sym, rel_type, strength))
    else:
        seen = set()
        for part in [p.strip() for p in text.split(";") if p.strip()] or [text]:
            for sym in parse_transporters(part):
                if sym not in seen:
                    seen.add(sym)
                    out.append((sym, rel_type, strength))
    return out


def load_fda_rows(filepath: str) -> list[dict]:
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"{filepath} not found. Copy the FDA Table 1 from "
            "https://www.fda.gov/drugs/drug-interactions-labeling/"
            "healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems "
            "into a spreadsheet and save as CSV."
        )

    with open(filepath, encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        rows = list(reader)

    if len(rows) < 2:
        raise ValueError(f"{filepath} is too short; expected header + data rows")

    # Row 0 is often an FDA page title; row 1 is the real header
    header_idx = 0
    if "Drug or Other Substance" not in rows[0][0]:
        for i, row in enumerate(rows[:5]):
            if row and "Drug or Other Substance" in row[0]:
                header_idx = i
                break

    header = [h.strip() for h in rows[header_idx]]
    data_rows = []
    for row in rows[header_idx + 1 :]:
        if not row or not row[0].strip():
            continue
        rec = {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
        data_rows.append(rec)
    log.info("FDA CSV: %d substance rows, header at line %d", len(data_rows), header_idx + 1)
    return data_rows


def build_edges(resolver: DrugResolver, fda_rows: list[dict]):
    """
    Returns:
      enzyme_edges: list of dicts for Enzyme relationships
      transporter_edges: list of dicts for Transporter relationships
      stats: counters
    """
    # Dedupe: (rxcui, symbol, rel_type, label) -> best strength
    enzyme_dedup = {}
    transporter_dedup = {}
    unmatched = set()
    substances_total = 0
    substances_matched = 0

    for rec in fda_rows:
        raw_name = rec.get("Drug or Other Substance", "").strip()
        if not raw_name:
            continue
        substances_total += 1
        clean = clean_fda_drug_name(raw_name)
        rxcui = resolver.resolve(clean)
        if not rxcui and " and " in clean:
            # Combo entries: try longest token first (e.g. atazanavir from combo)
            for piece in sorted(clean.split(" and "), key=len, reverse=True):
                rxcui = resolver.resolve(piece.strip())
                if rxcui:
                    break
        if not rxcui:
            unmatched.add(raw_name)
            continue
        substances_matched += 1

        for col, (rel_type, default_strength, kind) in COLUMN_SPECS.items():
            cell = rec.get(col, "")
            parsed = parse_cell(cell, rel_type, default_strength, kind)
            for symbol, rt, strength in parsed:
                key = (rxcui, symbol, rt)
                store = enzyme_dedup if kind == "enzyme" else transporter_dedup
                rank = STRENGTH_RANK.get(strength or "", 0)
                if key not in store or rank > STRENGTH_RANK.get(store[key].get("strength") or "", 0):
                    store[key] = {
                        "rxcui": rxcui,
                        "symbol": symbol,
                        "rel_type": rt,
                        "strength": strength,
                        "source": "fda_table1",
                        "fda_substance": raw_name,
                    }

    stats = {
        "substances_total": substances_total,
        "substances_matched": substances_matched,
        "unmatched": len(unmatched),
        "enzyme_edges": len(enzyme_dedup),
        "transporter_edges": len(transporter_dedup),
    }
    return list(enzyme_dedup.values()), list(transporter_dedup.values()), unmatched, stats


def insert_enzyme_layer(graph, enzyme_edges, transporter_edges):
    enzymes = {e["symbol"] for e in enzyme_edges}
    transporters = {e["symbol"] for e in transporter_edges}

    if enzymes:
        graph.query(
            "UNWIND $rows AS row MERGE (e:Enzyme {symbol: row.symbol})",
            {"rows": [{"symbol": s} for s in sorted(enzymes)]},
        )
    if transporters:
        graph.query(
            "UNWIND $rows AS row MERGE (t:Transporter {symbol: row.symbol})",
            {"rows": [{"symbol": s} for s in sorted(transporters)]},
        )

    rel_batches = [
        ("enzyme", enzyme_edges, "Enzyme"),
        ("transporter", transporter_edges, "Transporter"),
    ]

    for label, edges, node_label in rel_batches:
        if not edges:
            continue
        # Group by rel_type for simpler Cypher
        by_rel = defaultdict(list)
        for e in edges:
            by_rel[e["rel_type"]].append(e)

        for rel_type, chunk_list in by_rel.items():
            num_batches = math.ceil(len(chunk_list) / BATCH_SIZE)
            for batch_idx in range(num_batches):
                chunk = chunk_list[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
                sensitivity_set = ", r.sensitivity = row.strength" if rel_type == "SUBSTRATE_OF" else ""
                cypher = f"""
                UNWIND $rows AS row
                MATCH (d:Drug {{rxcui: row.rxcui}})
                MATCH (n:{node_label} {{symbol: row.symbol}})
                MERGE (d)-[r:{rel_type}]->(n)
                SET r.strength = row.strength{sensitivity_set},
                    r.source = row.source,
                    r.fda_substance = row.fda_substance
                """
                graph.query(cypher, {"rows": chunk})
            log.info("  %s %s edges: %d (%d batches)", node_label, rel_type, len(chunk_list), num_batches)


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


def run_health_checks(graph, resolver: DrugResolver, baseline_drug_count: int, stats: dict):
    log.info("")
    log.info("=" * 60)
    log.info("SESSION 3 HEALTH CHECKS")
    log.info("=" * 60)

    match_rate = stats["substances_matched"] / stats["substances_total"]
    assert match_rate >= 0.85, f"FAIL: FDA substance match rate {match_rate:.1%} < 85%"
    log.info("  PASS  %-45s  %.1f%%", "FDA substance match rate", match_rate * 100)

    assert_count_range(
        graph, "MATCH (e:Enzyme) RETURN count(e)", min_val=5, max_val=30, label="Enzyme node count"
    )
    assert_count_range(
        graph, "MATCH (t:Transporter) RETURN count(t)", min_val=3, max_val=20, label="Transporter node count"
    )

    for rel, min_edges in [("INHIBITS", 50), ("INDUCES", 20), ("SUBSTRATE_OF", 50)]:
        assert_count_range(
            graph,
            f"MATCH ()-[r:{rel}]->() RETURN count(r)",
            min_val=min_edges,
            label=f"{rel} edge count (any target)",
        )

    clar_rxcui = resolver.resolve("clarithromycin")
    sim_rxcui = resolver.resolve("simvastatin")
    assert clar_rxcui and sim_rxcui, "FAIL: clarithromycin or simvastatin not in lookup map"

    res = graph.query(
        "MATCH (d:Drug {rxcui: $r})-[i:INHIBITS]->(e:Enzyme {symbol: 'CYP3A4'}) "
        "RETURN i.strength",
        {"r": clar_rxcui},
    )
    assert res.result_set, "FAIL: clarithromycin -[:INHIBITS]-> CYP3A4 not found"
    log.info("  PASS  %-45s  strength=%s", "Clarithromycin inhibits CYP3A4", res.result_set[0][0])

    res = graph.query(
        "MATCH (d:Drug {rxcui: $r})-[:SUBSTRATE_OF]->(e:Enzyme {symbol: 'CYP3A4'}) "
        "RETURN count(*)",
        {"r": sim_rxcui},
    )
    assert res.result_set[0][0] >= 1, "FAIL: simvastatin not SUBSTRATE_OF CYP3A4"
    log.info("  PASS  %-45s  edge exists", "Simvastatin substrate of CYP3A4")

    # Multi-hop path spot-check (clarithromycin → CYP3A4 ← simvastatin)
    res = graph.query(
        "MATCH (a:Drug {rxcui: $cla})-[:INHIBITS]->(e:Enzyme {symbol: 'CYP3A4'})"
        "<-[:SUBSTRATE_OF]-(b:Drug {rxcui: $sim}) RETURN count(*)",
        {"cla": clar_rxcui, "sim": sim_rxcui},
    )
    assert res.result_set[0][0] >= 1, "FAIL: clarithromycin–CYP3A4–simvastatin path missing"
    log.info("  PASS  %-45s  path found", "Demo CYP3A4 mediation path")

    known_edges = [
        ("rifampin", "INDUCES", "CYP3A4", "Enzyme"),
        ("warfarin", "SUBSTRATE_OF", "CYP2C9", "Enzyme"),
        ("digoxin", "SUBSTRATE_OF", "P-gp", "Transporter"),
    ]
    for drug_name, rel_type, symbol, label in known_edges:
        rxcui = resolver.resolve(drug_name)
        assert rxcui, f"FAIL: {drug_name} not in lookup map"
        res = graph.query(
            f"MATCH (d:Drug {{rxcui: $rxcui}})-[r:{rel_type}]->"
            f"(target:{label} {{symbol: $symbol}}) RETURN r.strength, r.sensitivity",
            {"rxcui": rxcui, "symbol": symbol},
        )
        assert res.result_set, f"FAIL: {drug_name} -[:{rel_type}]-> {symbol} not found"
        log.info("  PASS  %-45s  %s", f"{drug_name} {rel_type} {symbol}", res.result_set[0])

    assert_zero(
        graph,
        "MATCH ()-[r:SUBSTRATE_OF]->(e:Enzyme) "
        "WHERE r.sensitivity IS NULL RETURN count(r)",
        label="Enzyme SUBSTRATE_OF edges without sensitivity",
    )

    assert_zero(
        graph,
        "MATCH (d)-[r:INHIBITS|INDUCES|SUBSTRATE_OF]->(x) "
        "WHERE NOT d:Drug OR (NOT x:Enzyme AND NOT x:Transporter) RETURN count(r)",
        label="Orphan enzyme-layer edges",
    )

    current_drugs = assert_count_range(
        graph,
        "MATCH (d:Drug) RETURN count(d)",
        min_val=baseline_drug_count,
        max_val=baseline_drug_count,
        label="Drug node count unchanged",
    )
    assert current_drugs == baseline_drug_count

    log.info("=" * 60)
    log.info("ALL SESSION 3 HEALTH CHECKS PASSED [SUCCESS]")
    log.info("=" * 60)


def clear_enzyme_layer(graph):
    for rel in ("INHIBITS", "INDUCES", "SUBSTRATE_OF"):
        graph.query(
            f"MATCH ()-[r:{rel}]->(x) WHERE x:Enzyme OR x:Transporter DELETE r"
        )
    graph.query("MATCH (e:Enzyme) DELETE e")
    graph.query("MATCH (t:Transporter) DELETE t")


def main():
    log.info("=" * 60)
    log.info("SESSION 3: FDA CYP / TRANSPORTER LAYER")
    log.info("=" * 60)

    if not os.path.exists(FDA_CSV):
        log.error("Missing %s — see module docstring for how to gather it.", FDA_CSV)
        sys.exit(1)

    log.info("Using FDA data file: %s", os.path.abspath(FDA_CSV))

    try:
        db = FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT)
        graph = db.select_graph(GRAPH_NAME)
    except Exception as exc:
        log.error("Failed to connect to FalkorDB: %s", exc)
        sys.exit(1)

    res = graph.query("MATCH (d:Drug) RETURN count(d)")
    baseline_drug_count = res.result_set[0][0]
    if baseline_drug_count == 0:
        log.error("No Drug nodes in graph. Run session1_rxnorm.py first.")
        sys.exit(1)
    log.info("Baseline Drug nodes: %d", baseline_drug_count)

    with open(LOOKUP_MAP_FILE, encoding="utf-8") as fh:
        name_to_rxcui = json.load(fh)
    resolver = DrugResolver(name_to_rxcui)

    log.info("Clearing prior Enzyme / Transporter layer (if re-run) ...")
    clear_enzyme_layer(graph)

    t0 = time.time()
    fda_rows = load_fda_rows(FDA_CSV)
    enzyme_edges, transporter_edges, unmatched, stats = build_edges(resolver, fda_rows)

    with open(UNMATCHED_FILE, "w", encoding="utf-8") as fh:
        for name in sorted(unmatched):
            fh.write(name + "\n")
    log.info("Wrote %d unmatched substance names to %s", len(unmatched), UNMATCHED_FILE)

    log.info(
        "Parsed FDA edges in %.1fs | substances matched %d/%d | enzyme edges=%d | transporter edges=%d",
        time.time() - t0,
        stats["substances_matched"],
        stats["substances_total"],
        stats["enzyme_edges"],
        stats["transporter_edges"],
    )

    insert_enzyme_layer(graph, enzyme_edges, transporter_edges)
    run_health_checks(graph, resolver, baseline_drug_count, stats)
    log.info("Next step: Run session4_openfda.py")


if __name__ == "__main__":
    main()
