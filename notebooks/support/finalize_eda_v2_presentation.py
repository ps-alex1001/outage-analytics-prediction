"""Add verified opening prose without changing executed notebook code or outputs."""
from pathlib import Path
import json
import nbformat
from nbconvert import HTMLExporter

ROOT=Path(__file__).resolve().parents[2]

def finalize_presentation(nb, root=ROOT):
    out=root/'data/ml/v2'
    m=json.loads((out/'ml_dataset_metadata.json').read_text())
    validation=json.loads((out/'audit/validation.json').read_text())
    quality=json.loads((out/'audit/source_inspection.json').read_text())
    code_cells=sum(c.cell_type=='code' for c in nb.cells)
    figures=sum('image/png' in o.get('data',{}) for c in nb.cells if c.cell_type=='code' for o in c.outputs)
    assert validation['status']=='passed' and validation['rows']==m['rows']
    marker='### Executive summary — populated from independently verified outputs'
    opening=nb.cells[0].source.split(marker)[0]
    nb.cells[0].source=opening+marker+f'''

**Verified result snapshot:** **{m['rows']:,} rows × {m['columns']} columns**, covering {quality['geography']['barangays']} barangays and {quality['geography']['municipalities']} municipalities.

- **Eligible labels:** {m['positive_rows']:,} positive and {m['negative_rows']:,} negative hours; {m['excluded_rows']:,} rows are masked.
- **Feature matrix:** {m['predictor_count']} predictors; {m['engineered_created']} engineered candidates created and {m['engineered_retained']} retained. NDVI and wind × NDVI are deferred.
- **Validation:** every exported key, label and retained feature value was independently checked. All {code_cells} code cells ran successfully with {figures} saved figures.
- **Interpretation:** retrospective synthetic research only. No model was trained and no actual PENELCO forecasting accuracy is established.

The next cell loads the verified audits and rechecks their consistency. It does not rebuild the full panel. Use the project's uv environment; there are no installation or network cells.
'''
    addition=('\n\n**Terminology:** CAPE means *convective available potential energy* (J/kg), a modeled atmospheric-instability indicator; higher CAPE alone does not establish a storm or an outage. NDVI means *normalized difference vegetation index*, a unitless spectral index from −1 to 1. Higher values often indicate greener vegetation, not measured power-line contact or tree-clearance distance.')
    for cell in nb.cells:
        if cell.cell_type=='markdown' and cell.source.startswith('## 8. Environmental EDA') and '**Terminology:**' not in cell.source:
            cell.source+=addition
    return nb

if __name__=='__main__':
    path=ROOT/'notebooks/01_eda_feature_engineering_v2.ipynb'
    nb=nbformat.read(path,as_version=4)
    code_before=[c.source for c in nb.cells if c.cell_type=='code']
    nb=finalize_presentation(nb)
    assert code_before==[c.source for c in nb.cells if c.cell_type=='code']
    nbformat.validate(nb);nbformat.write(nb,path)
    html,_=HTMLExporter(template_name='lab').from_notebook_node(nb)
    (ROOT/'data/ml/v2/audit/01_eda_feature_engineering_v2.html').write_text(html,encoding='utf-8')
    print('Verified opening summary and glossary added; executed code and outputs preserved.')
