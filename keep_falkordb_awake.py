"""Perform a small, read-only cloud check from an external scheduler."""
from settings import GRAPH_NAME, graph_client


def main():
    client = graph_client()
    if GRAPH_NAME not in client.list_graphs():
        raise RuntimeError('Configured graph is missing; restore its data before using the app.')
    result = client.select_graph(GRAPH_NAME).ro_query('MATCH (d:Drug) RETURN d.rxcui LIMIT 1')
    if not result.result_set:
        raise RuntimeError('Configured graph has no drug data; restore it before using the app.')
    print('FalkorDB graph is reachable and contains drug data.')


if __name__ == '__main__':
    main()
