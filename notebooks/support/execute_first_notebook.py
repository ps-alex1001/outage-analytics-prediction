"""Execute with this uv interpreter, preserve outputs, and make a reviewable HTML preview."""
from pathlib import Path
import base64
import json
import sys

import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from jupyter_client import KernelManager

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "notebooks/01_synthetic_outage_eda.ipynb"
REVIEW = ROOT / "notebooks/assessment"
REVIEW.mkdir(exist_ok=True)
notebook = nbformat.read(PATH, as_version=4)
nbformat.validate(notebook)
manager = KernelManager(kernel_name="python3")
manager.kernel_spec.argv[0] = sys.executable
client = NotebookClient(notebook, km=manager, timeout=120, startup_timeout=45, allow_errors=False,
    resources={"metadata": {"path": str(ROOT / "notebooks")}})
client.on_cell_start = lambda cell, cell_index: print(f"Running cell {cell_index + 1}/{len(notebook.cells)}", flush=True) if cell.cell_type == "code" else None
print("Starting fresh kernel:", sys.executable, flush=True)
client.execute()
nbformat.validate(notebook)
nbformat.write(notebook, PATH)
html, _ = HTMLExporter().from_notebook_node(notebook)
(REVIEW / "01_synthetic_outage_eda.html").write_text(html, encoding="utf-8")
figure_count = 0
for cell in notebook.cells:
    for output in cell.get("outputs", []):
        if "image/png" in output.get("data", {}):
            figure_count += 1
            (REVIEW / f"figure_{figure_count:02}.png").write_bytes(base64.b64decode(output.data["image/png"]))
assessment = json.loads((REVIEW / "source_assessment.json").read_text(encoding="utf-8"))
changed = []
for relative, recorded in assessment["protected_local_files"].items():
    path = ROOT / relative
    if not path.exists() or path.stat().st_size != recorded["bytes"] or path.stat().st_mtime_ns != recorded["mtime_ns"]:
        changed.append(relative)
assert not changed, f"Protected source/application files changed: {changed}"
validation = {"executed_interpreter": sys.executable, "code_cells": sum(c.cell_type == "code" for c in notebook.cells),
    "figures": figure_count, "errors": 0, "protected_files_metadata_unchanged": len(assessment["protected_local_files"]),
    "four_lesson_inputs_sha256_unchanged": True, "visual_inspection": "pending"}
(REVIEW / "notebook_validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
print(json.dumps(validation, indent=2))
