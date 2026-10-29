from pathlib import Path
import ast, tempfile, hashlib, json
import numpy as np, pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier, ExtraTreesClassifier, AdaBoostClassifier
from xgboost import XGBClassifier
from portable_classifier import PortableClassifier
from portable_export_v3 import portable_specification
source = Path(globals().get('WORKFLOW_PATH','train_ml_dataset_v3_colab.py')).read_text()
tree = ast.parse(source)
names = {'make_estimator','matrix','sample','preprocess','inputs','metrics'}
functions = ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names], type_ignores=[])
SEED=42
BASE=['x'+str(i) for i in range(24)]
MUNICIPALITIES=[f'{i:010d}' for i in range(12)]
exec(compile(functions,'v3_workflow_functions','exec'),globals())
frames={}
for month in range(1,4):
    rows=[]
    for municipality in MUNICIPALITIES:
        for i in range(10):
            rows.append({'prediction_time':pd.Timestamp(f'2022-{month:02d}-01',tz='Asia/Manila')+pd.Timedelta(hours=i),'barangay_psgc':municipality,'municipality_psgc':municipality,'outage_next_hour':int(i<2),'split':'train',**{k:float(i) for k in BASE}})
    frames[f'month={month}']=pd.DataFrame(rows)
with tempfile.TemporaryDirectory() as temp:
    RESULTS=Path(temp); LOCAL=Path(temp)
    frame=lambda path: frames[path.name].copy()
    STRATA=[{'path':p,'municipality_psgc':m,'negatives':8} for p in frames for m in MUNICIPALITIES]
    COUNTS={'train':{'positives':72}}
    x,y=sample(100)
    assert x.shape==(172,36) and y.sum()==72
    a,b=sample(100)
    assert np.array_equal(x,a) and np.array_equal(y,b)
    assert x[:,24:].sum(axis=0).min()>0 and (x[:,24:].sum(axis=1)==1).all()
    imputer,scaler,clean=preprocess(x)
    for name in ['Logistic Regression','Decision Tree','Random Forest','Histogram Gradient Boosting','XGBoost','Extra Trees','AdaBoost']:
        model=make_estimator(name); model.fit(inputs(name,clean,scaler),y)
        p=model.predict_proba(inputs(name,clean,scaler))[:,1]
        if name!='XGBoost':
            bundle={'name':name,'model':model,'scaler':scaler}
            spec=portable_specification(bundle)
            actual=PortableClassifier(spec,36).predict(clean)
            np.testing.assert_allclose(actual,p,rtol=1e-6,atol=1e-8)
        assert np.isfinite(p).all() and ((p>=0)&(p<=1)).all()
    p=np.r_[np.full(3,.9),np.full(1997,.1)].astype('float32')
    labels=np.zeros(2000,dtype='uint8'); labels[0]=1
    report=metrics(labels,p); assert report['tp']+report['fp']==0
    p[:2]=.95; report=metrics(labels,p); assert report['tp']+report['fp']==2
    print(json.dumps({'algorithms_smoke_fitted':7,'shared_sample_rows':len(y),'all_positives_retained':int(y.sum()),'exact_negatives':int((y==0).sum()),'categorical_columns':12,'tie_budget_controls':'passed','portable_serving_parity':'all six sklearn algorithms passed'},indent=2))
