"""Filesystem and graph configuration shared by runtime and data loaders."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
DATA_DIR = Path(os.environ.get('DRUG_DATA_DIR', str(ROOT / 'data'))).resolve()
HOST = os.environ.get('FALKORDB_HOST', 'localhost')
PORT = int(os.environ.get('FALKORDB_PORT', '6379'))
GRAPH_NAME = os.environ.get('FALKORDB_GRAPH', 'drug_interactions')


def graph_client():
    """Connect to local FalkorDB or a credential-protected cloud instance."""
    from falkordb import FalkorDB
    tls = os.environ.get('FALKORDB_SSL', 'false').lower() == 'true'
    return FalkorDB(
        host=HOST, port=PORT,
        username=os.environ.get('FALKORDB_USERNAME') or None,
        password=os.environ.get('FALKORDB_PASSWORD') or None,
        ssl=tls, ssl_check_hostname=tls,
        socket_connect_timeout=15, socket_timeout=60,
    )
