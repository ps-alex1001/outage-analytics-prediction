"""Execute and export the V2 notebook with this project's exact interpreter."""
from pathlib import Path
import json
import sys
import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from jupyter_client import KernelManager
from finalize_eda_v2_presentation import finalize_presentation

ROOT=Path(__file__).resolve().parents[2] if Path(__file__).parent.name=='support' else Path(__file__).resolve().parents[1]
path=ROOT/'notebooks/01_eda_feature_engineering_v2.ipynb'
nb=nbformat.read(path,as_version=4)
manager=KernelManager(kernel_name='python3')
manager.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
client=NotebookClient(nb,timeout=300,kernel_name='python3',km=manager,
    resources={'metadata':{'path':str(ROOT)}},record_timing=True,startup_timeout=45)
client.on_cell_start=lambda cell,cell_index: print(f'Executing cell {cell_index+1}/{len(nb.cells)}',flush=True) if cell.cell_type=='code' else None
print('Starting project Jupyter kernel:',sys.executable,flush=True)
client.execute()
nb=finalize_presentation(nb,ROOT)
nbformat.validate(nb)
nbformat.write(nb,path)
exporter=HTMLExporter(template_name='lab')
html,_=exporter.from_notebook_node(nb)
destination=ROOT/'data/ml/v2/audit/01_eda_feature_engineering_v2.html'
destination.write_text(html,encoding='utf-8')
report={'status':'passed','code_cells':sum(c.cell_type=='code' for c in nb.cells),
    'executed_code_cells':sum(c.cell_type=='code' and c.execution_count is not None for c in nb.cells),
    'errors':sum(o.output_type=='error' for c in nb.cells if c.cell_type=='code' for o in c.outputs),
    'image_outputs':sum('image/png' in o.get('data',{}) for c in nb.cells if c.cell_type=='code' for o in c.outputs),
    'html':str(destination),'interpreter':sys.executable}
(destination.parent/'notebook_execution.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2))
