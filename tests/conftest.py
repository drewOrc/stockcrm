"""Tests run against a standalone database: the CRM bootstraps the engine-owned tables and writes the
knowledge layer directly instead of through the engine's REST API."""
import os

os.environ["STOCKCRM_BOOTSTRAP_KNOWLEDGE"] = "1"
os.environ["STOCKCRM_KNOWLEDGE_WRITES"] = "direct"
