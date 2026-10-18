#!/usr/bin/env python3
"""Seed the graph with synthetic patient prescriptions in the canonical schema."""

import json
from pathlib import Path

from falkordb import FalkorDB


from settings import DATA_DIR, HOST, PORT, GRAPH_NAME


def load_demo_patients(path: str | Path | None = None) -> list[dict]:
    if path is None:
        path = Path(__file__).with_name("demo_patients.json")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload["patients"]


def resolve_rxcui(name: str, mapping: dict[str, str]) -> str | None:
    key = (name or "").strip().lower()
    return mapping.get(key)


def seed_demo_patients(graph=None, patients=None) -> dict[str, int]:
    if graph is None:
        graph = FalkorDB(host=HOST, port=PORT).select_graph(GRAPH_NAME)
    if patients is None:
        patients = load_demo_patients()

    with (DATA_DIR / "name_to_rxcui.json").open(encoding="utf-8") as source:
        name_map = json.load(source)
    resolved = {}
    for patient in patients:
        for med in patient["medications"]:
            drug_name = med["drug"].strip().lower()
            rxcui = resolve_rxcui(drug_name, name_map)
            if rxcui is None:
                raise ValueError(f"Drug {drug_name!r} could not be resolved to an RXCUI")
            resolved[drug_name] = str(rxcui)
    found = graph.query(
        "MATCH (d:Drug) WHERE d.rxcui IN $ids RETURN collect(d.rxcui)",
        {"ids": sorted(set(resolved.values()))},
    ).result_set
    available = {str(value) for value in (found[0][0] if found and found[0][0] else [])}
    missing = sorted(set(resolved.values()) - available)
    if missing:
        raise ValueError(f"Resolved RXCUIs missing from Drug nodes: {', '.join(missing)}")

    patient_ids = [patient["id"] for patient in patients]
    legacy_ids = [f"P{number}" for number in range(1, 6)]
    graph.query(
        "MATCH (p:Patient)-[t:TAKES]->(d:Drug) "
        "WHERE p.id IN $patient_ids OR p.id IN $legacy_ids DELETE t",
        {"patient_ids": patient_ids, "legacy_ids": legacy_ids},
    )
    existing = graph.query(
        "MATCH (p:Patient)-[:TAKES]->(rx:Prescription) "
        "WHERE p.id IN $patient_ids RETURN p.id, rx.id",
        {"patient_ids": patient_ids},
    ).result_set
    managed_ids = [
        rx_id
        for patient_id, rx_id in existing
        if rx_id and str(rx_id).startswith(f"{patient_id}-")
    ]
    if managed_ids:
        graph.query(
            "MATCH (rx:Prescription) WHERE rx.id IN $ids DETACH DELETE rx",
            {"ids": managed_ids},
        )

    graph.query(
        "UNWIND $patients AS patient "
        "MERGE (p:Patient {id: patient.id}) "
        "SET p.name = patient.name, p.age = patient.age, p.gender = patient.gender",
        {"patients": patients},
    )

    prescription_count = 0
    for patient in patients:
        for index, med in enumerate(patient["medications"], 1):
            drug_name = med["drug"]
            rxcui = resolved[drug_name.strip().lower()]
            rx_id = f"{patient['id']}-{index:02d}"
            graph.query(
                "MATCH (p:Patient {id: $pid}), (d:Drug {rxcui: $rxcui}) "
                "MERGE (rx:Prescription {id: $rx_id}) "
                "SET rx.drug_rxcui = $rxcui, rx.drug_name = d.name, rx.dose = $dose, "
                "    rx.frequency = $frequency, rx.start_date = $start_date, "
                "    rx.session5_fixture = true "
                "MERGE (p)-[:TAKES]->(rx) "
                "MERGE (rx)-[:OF_DRUG]->(d)",
                {
                    "pid": patient["id"],
                    "rxcui": rxcui,
                    "rx_id": rx_id,
                    "dose": med.get("dose", ""),
                    "frequency": med.get("frequency", ""),
                    "start_date": med.get("start_date", ""),
                },
            )
            prescription_count += 1

    return {"patients": len(patients), "prescriptions": prescription_count}


def run_health_checks(graph=None) -> None:
    if graph is None:
        graph = FalkorDB(host=HOST, port=PORT).select_graph(GRAPH_NAME)

    patients = load_demo_patients()
    patient_ids = [patient["id"] for patient in patients]
    expected_prescriptions = sum(len(patient["medications"]) for patient in patients)
    found_patients = graph.query(
        "MATCH (p:Patient) WHERE p.id IN $ids RETURN p.id, count(p)", {"ids": patient_ids}
    ).result_set
    assert len(found_patients) == len(patient_ids) and all(row[1] == 1 for row in found_patients), (
        f"Expected each fixture patient exactly once, found {found_patients}"
    )

    fixture_paths = graph.query(
        "MATCH (rx:Prescription {session5_fixture: true}) "
        "OPTIONAL MATCH (p:Patient)-[:TAKES]->(rx) "
        "OPTIONAL MATCH (rx)-[:OF_DRUG]->(d:Drug) "
        "RETURN rx.id, collect(DISTINCT p.id), collect(DISTINCT d.rxcui) ORDER BY rx.id"
    ).result_set
    assert len(fixture_paths) == expected_prescriptions and all(
        len(row[1]) == 1 and len(row[2]) == 1 for row in fixture_paths
    ), (
        f"Expected {expected_prescriptions} prescriptions with one owner and one drug each, "
        f"got {fixture_paths}"
    )

    direct_links = graph.query(
        "MATCH (p:Patient)-[:TAKES]->(d:Drug) "
        "WHERE p.id IN $ids OR p.id IN $legacy_ids RETURN count(*)",
        {"ids": patient_ids, "legacy_ids": [f"P{number}" for number in range(1, 6)]},
    ).result_set[0][0]
    assert direct_links == 0, f"Legacy demo patient->drug links remain: {direct_links}"

    scenario_query = (
        "MATCH (p:Patient {id:'PAT001'})-[:TAKES]->(:Prescription)-[:OF_DRUG]->(cur:Drug) "
        "MATCH (cur)-[inh:INHIBITS]->(e:Enzyme)<-[:SUBSTRATE_OF]-(proposed:Drug {name_lower:'simvastatin'}) "
        "RETURN count(*) AS c"
    )
    scenario_hits = graph.query(scenario_query).result_set[0][0]
    assert scenario_hits > 0, "PAT001 scenario did not produce the expected CYP3A4 evidence path"


if __name__ == "__main__":
    graph = FalkorDB(host=HOST, port=PORT).select_graph(GRAPH_NAME)
    result = seed_demo_patients(graph=graph, patients=load_demo_patients())
    run_health_checks(graph)
    print(json.dumps(result, indent=2))
    print("Session 5 patient/prescription seeding passed.")