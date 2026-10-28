"""Execute Notebook 02's V2 analytical report and verify protected inputs."""
from pathlib import Path
import base64
import hashlib
import json
import sys
import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from jupyter_client import KernelManager

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "notebooks/02_weather_coverage_and_event_context.ipynb"
REVIEW = ROOT / "notebooks/assessment/notebook02"
REVIEW.mkdir(exist_ok=True)
protected = {str(p.relative_to(ROOT)): (p.stat().st_size, p.stat().st_mtime_ns)
    for p in (ROOT / "data").rglob("*") if p.is_file()}
first_notebook = ROOT / "notebooks/01_synthetic_outage_eda.ipynb"
def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
first_hash = file_hash(first_notebook)
other_notebook_hashes = {str(path): file_hash(path) for path in (ROOT / "notebooks").glob("*.ipynb") if path != PATH}
notebook = nbformat.read(PATH, as_version=4)
nbformat.validate(notebook)
manager = KernelManager(kernel_name="python3")
manager.kernel_spec.argv[0] = sys.executable
client = NotebookClient(notebook, km=manager, timeout=120, startup_timeout=45, allow_errors=False,
    resources={"metadata": {"path": str(ROOT / "notebooks")}})
client.on_cell_start = lambda cell, cell_index: print(f"Running cell {cell_index+1}/{len(notebook.cells)}", flush=True) if cell.cell_type == "code" else None
print("Starting project kernel:", sys.executable, flush=True)
client.execute()
nbformat.validate(notebook)
nbformat.write(notebook, PATH)
html, _ = HTMLExporter().from_notebook_node(notebook)
(REVIEW / "02_weather_coverage_and_event_context.html").write_text(html, encoding="utf-8")
figures = 0
for cell in notebook.cells:
    for output in cell.get("outputs", []):
        if "image/png" in output.get("data", {}):
            figures += 1
            (REVIEW / f"figure_{figures:02}.png").write_bytes(base64.b64decode(output.data["image/png"]))
for relative, before in protected.items():
    path = ROOT / relative
    assert path.exists() and (path.stat().st_size, path.stat().st_mtime_ns) == before, f"Source changed: {relative}"
assert file_hash(first_notebook) == first_hash, "Notebook 01 changed."
assert all(file_hash(Path(path)) == digest for path, digest in other_notebook_hashes.items()), "Another notebook changed."
validation = {"interpreter": sys.executable, "code_cells": sum(c.cell_type == "code" for c in notebook.cells),
    "figures": figures, "errors": 0, "protected_data_files_metadata_unchanged": len(protected),
    "analysis_input_hashes_unchanged": True, "other_notebooks_hashes_unchanged": True,
    "main_dataset": notebook.metadata.get("event_source"), "analysis_style": notebook.metadata.get("analysis_style"),
    "notebook01_hash_unchanged": True, "visual_review": "pending"}
(REVIEW / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
print(json.dumps(validation, indent=2))
