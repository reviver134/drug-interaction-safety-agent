"""MCP tools for prescription context and evidence from the FalkorDB graph."""

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

import prescription_service as service
from session5_patients import seed_demo_patients as seed_fixture
import prescription_analysis
import gemini_service


mcp = FastMCP("drug-interaction-evidence")


@mcp.tool()
def resolve_medications(medications: list[str]) -> dict:
    """Match confirmed names and aliases; return all supported combination ingredients."""
    return {'medications': prescription_analysis.resolve_list(service.connect_graph(), medications)}


@mcp.tool()
def analyze_prescription(medications: list[str], confirmed: bool = False) -> dict:
    """Check every ingredient pair after name review; explain only returned evidence."""
    if not confirmed:
        return {'status': 'CONFIRMATION_REQUIRED', 'message': 'Review medication names and ingredients first.'}
    result = prescription_analysis.analyze_medications(service.connect_graph(), medications)
    return {**result, 'explanation': gemini_service.explain_analysis(result)}


@mcp.tool()
def get_patient_context(patient_id: str) -> dict:
    """Return a patient's current prescriptions through TAKES and OF_DRUG paths."""
    return service.get_patient_context(service.connect_graph(), patient_id)


@mcp.tool()
def find_interaction_paths(patient_id: str, proposed_drug: str) -> dict:
    """Resolve a drug and return DDInter and CYP/transporter paths from FalkorDB."""
    return service.find_interaction_paths(service.connect_graph(), patient_id, proposed_drug)


@mcp.tool()
def check_prescription(patient_id: str, proposed_drug: str) -> dict:
    """Apply deterministic demo rules to graph evidence and return an explanation."""
    return service.check_prescription(service.connect_graph(), patient_id, proposed_drug)


@mcp.tool()
def change_patient_medication(patient_id: str, drug: str, action: str) -> dict:
    """Add or remove a medication through a Prescription node; action is add or remove."""
    return service.change_patient_medication(
        service.connect_graph(), patient_id, drug, action
    )


@mcp.tool()
def seed_demo_patients() -> dict:
    """Restore the canonical synthetic patient fixture after a demo medication change."""
    return seed_fixture(service.connect_graph())


if __name__ == "__main__":
    mcp.run()
