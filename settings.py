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
