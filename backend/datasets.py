"""Pinned dataset paths shared by overview and spatial-event services."""
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
OUTAGES_PATH = DATA_ROOT / 'synthetic/v2_20261024T181237_896469Z/synthetic_outage_events_v2.csv'
IMPACTS_PATH = DATA_ROOT / 'impacts/v2_topology_1_2_0'
