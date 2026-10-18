"""Shared graph-backed prescription context, evidence, and deterministic rules."""

from __future__ import annotations

import time
from typing import Any

from falkordb import FalkorDB


from settings import HOST as FALKORDB_HOST, PORT as FALKORDB_PORT, GRAPH_NAME

PATIENT_CONTEXT_QUERY = """
MATCH (p:Patient {id: $patient_id})
OPTIONAL MATCH (p)-[:TAKES]->(rx:Prescription)-[:OF_DRUG]->(d:Drug)
RETURN p.id, p.name, p.age, rx.id, rx.drug_rxcui, rx.drug_name,
       rx.dose, rx.frequency, rx.start_date, d.rxcui, d.name, d.name_lower
ORDER BY d.name_lower
"""

PRESCRIPTION_PATHS_QUERY = """
MATCH (p:Patient {id: $patient_id})-[:TAKES]->(:Prescription)-[:OF_DRUG]->(cur:Drug)
      -[a:INHIBITS|INDUCES]->(e)<-[s:SUBSTRATE_OF]-(new:Drug {rxcui: $proposed_rxcui})
WHERE (e:Enzyme OR e:Transporter) AND cur.rxcui <> new.rxcui
RETURN 'mechanism' AS kind, 'incoming' AS direction, cur.name AS current_drug, cur.rxcui AS current_rxcui,
       new.name AS proposed_drug, new.rxcui AS proposed_rxcui, type(a) AS relationship,
       a.strength AS strength, e.symbol AS target, labels(e)[0] AS target_type,
       s.strength AS substrate_class, s.sensitivity AS substrate_sensitivity,
       '' AS ddinter_id_a, '' AS ddinter_id_b,
       coalesce(a.source, 'FDA CYP/transporter table') AS source_reference
UNION
MATCH (p:Patient {id: $patient_id})-[:TAKES]->(:Prescription)-[:OF_DRUG]->(cur:Drug)
      -[s:SUBSTRATE_OF]->(e)<-[a:INHIBITS|INDUCES]-(new:Drug {rxcui: $proposed_rxcui})
WHERE (e:Enzyme OR e:Transporter) AND cur.rxcui <> new.rxcui
RETURN 'mechanism' AS kind, 'outgoing' AS direction, cur.name AS current_drug, cur.rxcui AS current_rxcui,
       new.name AS proposed_drug, new.rxcui AS proposed_rxcui, type(a) AS relationship,
       a.strength AS strength, e.symbol AS target, labels(e)[0] AS target_type,
       s.strength AS substrate_class, s.sensitivity AS substrate_sensitivity,
       '' AS ddinter_id_a, '' AS ddinter_id_b,
       coalesce(a.source, 'FDA CYP/transporter table') AS source_reference
UNION
MATCH (p:Patient {id: $patient_id})-[:TAKES]->(:Prescription)-[:OF_DRUG]->(cur:Drug)
      -[r:INTERACTS_WITH]-(new:Drug {rxcui: $proposed_rxcui})
WHERE cur.rxcui <> new.rxcui
RETURN 'ddinter' AS kind, 'direct' AS direction, cur.name AS current_drug, cur.rxcui AS current_rxcui,
       new.name AS proposed_drug, new.rxcui AS proposed_rxcui,
       'INTERACTS_WITH' AS relationship, r.severity AS strength,
       '' AS target, '' AS target_type, '' AS substrate_class, '' AS substrate_sensitivity,
       coalesce(r.ddinter_id_a, '') AS ddinter_id_a,
       coalesce(r.ddinter_id_b, '') AS ddinter_id_b,
       coalesce(r.source, 'DDInter') AS source_reference
"""


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def connect_graph() -> Any:
    return FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT).select_graph(GRAPH_NAME)


def _query(
    graph: Any,
    trace: list[dict[str, Any]],
    name: str,
    cypher: str,
    params: dict[str, Any] | None = None,
) -> list[list[Any]]:
    started = time.perf_counter()
    result = graph.query(cypher, params or {})
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    rows = result.result_set
    trace.append(
        {
            "query": name,
            "cypher": cypher.strip(),
            "parameters": params or {},
            "returned_rows": len(rows),
            "elapsed_ms": elapsed_ms,
        }
    )
    return rows


def resolve_drug(graph: Any, name: str, trace: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    query_trace = trace if trace is not None else []
    normalized = (name or "").strip().lower()
    if not normalized:
        return None
    rows = _query(
        graph,
        query_trace,
        "resolve_exact_name",
        "MATCH (d:Drug {name_lower: $name}) RETURN d.rxcui, d.name, d.name_lower LIMIT 1",
        {"name": normalized},
    )
    if rows:
        return {"rxcui": rows[0][0], "name": rows[0][1], "name_lower": rows[0][2], "match": "exact"}
    from medication_aliases import REGIONAL_NAMES
    if normalized in REGIONAL_NAMES:
        reviewed = resolve_drug(graph, REGIONAL_NAMES[normalized], query_trace)
        if reviewed:
            return {**reviewed, "match": "reviewed_alias", "matched_alias": normalized}
    from medication_aliases import alias_index
    index = alias_index()
    ids = index['aliases'].get(normalized, [])
    if len(ids) != 1 or normalized in index['combination_brands']:
        return None
    rows = _query(graph, query_trace, 'resolve_verified_alias',
                  'MATCH (d:Drug) WHERE d.rxcui IN $ids RETURN d.rxcui, d.name, d.name_lower',
                  {'ids': ids})
    if len(rows) == 1:
        return {"rxcui": rows[0][0], "name": rows[0][1], "name_lower": rows[0][2], "match": "alias"}

    return None


def get_patient_context(
    graph: Any, patient_id: str, trace: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    query_trace = trace if trace is not None else []
    rows = _query(
        graph,
        query_trace,
        "get_patient_context",
        PATIENT_CONTEXT_QUERY,
        {"patient_id": patient_id},
    )
    if not rows:
        return {"status": "UNKNOWN_PATIENT", "patient_id": patient_id, "medications": []}
    first = rows[0]
    medications = [
        {
            "prescription_id": row[3],
            "drug_rxcui": row[9],
            "drug_name": row[10],
            "drug_name_lower": row[11],
            "dose": row[6],
            "frequency": row[7],
            "start_date": row[8],
        }
        for row in rows
        if row[9] is not None
    ]
    return {
        "status": "OK",
        "patient_id": first[0],
        "name": first[1],
        "age": first[2],
        "medications": medications,
    }


def find_interaction_paths(graph: Any, patient_id: str, proposed_drug: str) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    resolved = resolve_drug(graph, proposed_drug, trace)
    if resolved is None:
        return {
            "status": "UNKNOWN_DRUG",
            "patient_id": patient_id,
            "proposed_input": proposed_drug,
            "proposed_drug": None,
            "paths": [],
            "trace": trace,
        }

    context = get_patient_context(graph, patient_id, trace)
    if context["status"] != "OK":
        return {
            "status": "UNKNOWN_PATIENT",
            "patient_id": patient_id,
            "proposed_input": proposed_drug,
            "proposed_drug": resolved,
            "paths": [],
            "trace": trace,
        }

    rows = _query(
        graph,
        trace,
        "find_interaction_paths",
        PRESCRIPTION_PATHS_QUERY,
        {"patient_id": patient_id, "proposed_rxcui": resolved["rxcui"]},
    )
    paths = parse_evidence_rows(rows)
    return {
        "status": "OK",
        "patient_id": patient_id,
        "patient_context": context,
        "proposed_input": proposed_drug,
        "proposed_drug": resolved,
        "paths": paths,
        "trace": trace,
    }


def parse_evidence_rows(rows):
    columns = (
        "kind", "direction", "current_drug", "current_rxcui", "proposed_drug", "proposed_rxcui",
        "relationship", "strength", "target", "target_type", "substrate_class",
        "substrate_sensitivity", "ddinter_id_a", "ddinter_id_b", "source_reference",
    )
    paths = []
    for row in rows:
        evidence = dict(zip(columns, row))
        if evidence["kind"] == "mechanism":
            if evidence["direction"] == "incoming":
                evidence["actor_drug"] = evidence["current_drug"]
                evidence["actor_rxcui"] = evidence["current_rxcui"]
                evidence["substrate_drug"] = evidence["proposed_drug"]
                evidence["substrate_rxcui"] = evidence["proposed_rxcui"]
            else:
                evidence["actor_drug"] = evidence["proposed_drug"]
                evidence["actor_rxcui"] = evidence["proposed_rxcui"]
                evidence["substrate_drug"] = evidence["current_drug"]
                evidence["substrate_rxcui"] = evidence["current_rxcui"]
        for key in (
            "target", "target_type", "substrate_class", "substrate_sensitivity",
            "ddinter_id_a", "ddinter_id_b",
        ):
            if evidence[key] == "":
                evidence[key] = None
        if evidence["kind"] == "ddinter":
            source_ids = [evidence["ddinter_id_a"], evidence["ddinter_id_b"]]
            source_ids = [source_id for source_id in source_ids if source_id]
            evidence["evidence_id"] = "ddinter:" + ":".join(source_ids) if source_ids else None
        else:
            evidence["evidence_id"] = (
                f"mechanism:{evidence['actor_rxcui']}:{evidence['relationship']}:"
                f"{evidence['target']}:{evidence['substrate_rxcui']}"
            )
        paths.append(evidence)
    paths.sort(key=lambda path: (path["kind"], str(path["current_rxcui"]), str(path["target"])))
    return paths


def _classify(path: dict[str, Any]) -> tuple[str, str]:
    strength = str(path.get("strength") or "").strip().lower()
    if path["kind"] == "ddinter":
        if strength == "major":
            return "BLOCK", "DDInter reports a Major interaction."
        if strength == "moderate":
            return "FLAG", "DDInter reports a Moderate interaction."
        if strength == "minor":
            return "NOTE", "DDInter reports a Minor interaction."
        return "INSUFFICIENT_EVIDENCE", "DDInter severity is unknown; no risk level is inferred."

    sensitivity = " ".join(
        str(path.get(key) or "").lower()
        for key in ("substrate_class", "substrate_sensitivity")
    )
    is_sensitive = "sensitive" in sensitivity
    action = path.get("relationship")
    if action == "INHIBITS" and strength == "strong" and is_sensitive:
        return "BLOCK", "A strong inhibitor affects a substrate marked sensitive in the mechanism path."
    if action in {"INHIBITS", "INDUCES"} and strength in {"strong", "moderate"}:
        return "FLAG", f"A {strength} {action.lower()} mechanism path was found."
    return "NOTE", "A mechanism path was found, but the demo rule does not assign a higher level."


def check_prescription(graph: Any, patient_id: str, proposed_drug: str) -> dict[str, Any]:
    result = find_interaction_paths(graph, patient_id, proposed_drug)
    return explain_evidence(result)


def explain_evidence(result):
    status = result["status"]
    if status != "OK":
        summary = (
            "Patient was not found in the graph."
            if status == "UNKNOWN_PATIENT"
            else "Proposed drug did not resolve to a Drug node."
        )
        return {**result, "verdict": status, "summary": summary, "decision_basis": []}

    classified = [(path, *_classify(path)) for path in result["paths"]]
    order = {"BLOCK": 4, "FLAG": 3, "INSUFFICIENT_EVIDENCE": 2, "NOTE": 1}
    if not classified:
        verdict = "NO_KNOWN_INTERACTION"
        summary = "No interaction path was found in the loaded datasets; this is not a safety determination."
        basis = []
    else:
        highest = max(classified, key=lambda item: order.get(item[1], 0))
        verdict, summary = highest[1], highest[2]
        basis = [
            {"evidence_id": path["evidence_id"], "rule": rule}
            for path, level, rule in classified
            if level == verdict
        ]

    explanations = []
    for path in result["paths"]:
        if path["kind"] == "ddinter":
            meaning = {
                'major': 'The source places this interaction in its highest reported severity category. A pharmacist or prescriber should review this combination.',
                'moderate': 'The source reports an interaction that may need monitoring or changes to treatment. Ask a pharmacist or prescriber to review it.',
                'minor': 'The source rates this interaction as lower severity. This rating does not establish that the combination is suitable for you.',
            }.get(str(path['strength'] or '').lower(), 'The source does not provide a recognized severity rating; the level of concern is unclear.')
            explanations.append(
                f"For {path['current_drug']} + {path['proposed_drug']}: {meaning} "
                f"This loaded DDInter record gives a severity rating but no description of the specific effect. "
                f"(Source: {path.get('source_reference') or 'DDInter'}, {path['evidence_id']})"
            )
        else:
            target = f"{path['target_type']} {path['target']}".strip()
            explanations.append(
                f"{path['actor_drug']} {'reduces' if path['relationship'] == 'INHIBITS' else 'increases'} "
                f"the activity of {target}, a protein involved in how the body handles "
                f"{path['substrate_drug']}. This may change how the body handles that medicine. "
                f"This mechanism record alone does not establish the effect in an individual. "
                f"(Source: {path.get('source_reference')}, {path['evidence_id']})"
            )
    return {
        **result,
        "verdict": verdict,
        "summary": summary,
        "explanation": explanations,
        "decision_basis": basis,
        "disclaimer": "Deterministic hackathon demo rules over loaded public data; not clinical advice.",
    }


def change_patient_medication(graph: Any, patient_id: str, drug: str, action: str) -> dict[str, Any]:
    if action not in {"add", "remove"}:
        raise ValueError("action must be 'add' or 'remove'")
    context = get_patient_context(graph, patient_id)
    if context["status"] != "OK":
        return {"status": "UNKNOWN_PATIENT", "patient_id": patient_id}
    resolved = resolve_drug(graph, drug)
    if resolved is None:
        return {"status": "UNKNOWN_DRUG", "patient_id": patient_id, "drug": drug}
    if action == "add":
        if any(str(m["drug_rxcui"]) == str(resolved["rxcui"]) for m in context["medications"]):
            return {"status": "OK", "action": action, "patient_id": patient_id, "drug": resolved}
        graph.query(
            "MATCH (p:Patient {id: $patient_id}), (d:Drug {rxcui: $rxcui}) "
            "MERGE (rx:Prescription {id: $prescription_id}) "
            "SET rx.drug_rxcui = d.rxcui, rx.drug_name = d.name, rx.dose = '', "
            "rx.frequency = '', rx.start_date = '', rx.session5_fixture = false "
            "MERGE (p)-[:TAKES]->(rx) MERGE (rx)-[:OF_DRUG]->(d)",
            {
                "patient_id": patient_id,
                "rxcui": resolved["rxcui"],
                "prescription_id": f"ADHOC-{patient_id}-{resolved['rxcui']}",
            },
        )
    else:
        graph.query(
            "MATCH (p:Patient {id: $patient_id})-[:TAKES]->(rx:Prescription) "
            "-[:OF_DRUG]->(d:Drug {rxcui: $rxcui}) DETACH DELETE rx",
            {"patient_id": patient_id, "rxcui": resolved["rxcui"]},
        )
    return {"status": "OK", "action": action, "patient_id": patient_id, "drug": resolved}
