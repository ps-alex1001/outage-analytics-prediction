"""V3 seven-algorithm workflow. Called by a duplicate of the reviewed Colab notebook."""
import os
os.environ['OMP_NUM_THREADS']='2'
os.environ['OPENBLAS_NUM_THREADS']='2'
from pathlib import Path
import hashlib, json, time, shutil, gc, warnings, platform, zipfile
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier, ExtraTreesClassifier, AdaBoostClassifier
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss
from xgboost import XGBClassifier
import xgboost, joblib

SEED=42
ROOT=Path('/content/drive/MyDrive/PENELCO_ML_V3')
RESULTS=ROOT/'training_outputs/reviewed_v3'
RESULTS.mkdir(parents=True,exist_ok=True)
LOCAL=Path('/content/penelco_v3')
LOCAL.mkdir(exist_ok=True)
META=json.loads((ROOT/'ml_dataset_metadata.json').read_text())
BASE=json.loads((ROOT/'ml_predictor_columns.json').read_text())
SPLITS=json.loads((ROOT/'ml_split_manifest.json').read_text())
assert META['dataset_version']=='3.0.0' and len(BASE)==24
DATA_ID=hashlib.sha256((ROOT/'ml_dataset_metadata.json').read_bytes()).hexdigest()
assert DATA_ID=='a016e84213b7be61e6f5cb697921600965636d2b5491d5863f69e504ec2152a7', 'This experiment is pinned to the independently validated V3 export'
def save(name,value):
    (RESULTS/name).write_text(json.dumps(value,indent=2,allow_nan=False,default=str))
print('Staging unchanged Parquet partitions to local Colab storage...',flush=True)
for part in META['partitions']:
    relative=Path(part['path'].replace(chr(92),'/'))
    source=ROOT/relative; destination=LOCAL/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    if not destination.exists(): shutil.copy2(source,destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest()==part['sha256'], 'Partition checksum mismatch'
FILES=sorted((LOCAL/'ml_dataset_v3').glob('year=*/month=*/part-000.parquet'))
assert len(FILES)==len(META['partitions'])
MUNICIPALITIES=sorted(set(pq.ParquetFile(FILES[0]).read(columns=['municipality_psgc']).column(0).to_pylist()))
assert len(MUNICIPALITIES)==12 and all(isinstance(p,str) and len(p)==10 and p.isdigit() for p in MUNICIPALITIES)
FEATURES=BASE+['municipality_'+p for p in MUNICIPALITIES]
MODELS=['Logistic Regression','Decision Tree','Random Forest','Histogram Gradient Boosting','XGBoost','Extra Trees','AdaBoost']
SIGNATURE={'workflow_sha256':globals().get('WORKFLOW_SHA256',hashlib.sha256(Path(__file__).read_bytes()).hexdigest() if '__file__' in globals() else 'embedded'), 'dataset_id':DATA_ID,'features':FEATURES,'seed':SEED,'target':SPLITS['target'],
    'target_interval':'(t,t+1h]','benchmark_negatives':400000,'confirmation_negatives':1200000,
    'selection':'Shared stratified sample; full 2024 validation AP ranks all seven. Top two repeat on 1.2M negatives; confirmation AP selects the global finalist. No practical-model substitution.',
    'primary_alert_budget_per_1000':1,'test_opened_after_selection':True}

def make_estimator(name):
    if name=='Logistic Regression': return LogisticRegression(max_iter=350,solver='lbfgs',random_state=SEED)
    if name=='Decision Tree': return DecisionTreeClassifier(max_depth=8,min_samples_leaf=35,random_state=SEED)
    if name=='Random Forest': return RandomForestClassifier(n_estimators=80,max_depth=12,min_samples_leaf=15,n_jobs=2,random_state=SEED)
    if name=='Histogram Gradient Boosting': return HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=15,min_samples_leaf=35,learning_rate=.07,l2_regularization=1.,early_stopping=False,random_state=SEED)
    if name=='XGBoost': return XGBClassifier(n_estimators=220,max_depth=4,learning_rate=.07,subsample=.85,colsample_bytree=.9,max_bin=128,tree_method='hist',n_jobs=2,objective='binary:logistic',eval_metric='logloss',random_state=SEED)
    if name=='Extra Trees': return ExtraTreesClassifier(n_estimators=80,max_depth=12,min_samples_leaf=15,n_jobs=2,random_state=SEED)
    if name=='AdaBoost': return AdaBoostClassifier(n_estimators=90,learning_rate=.5,random_state=SEED)
    raise ValueError(name)

SIGNATURE['library_versions']={'sklearn':sklearn.__version__,'xgboost':xgboost.__version__}
SIGNATURE['model_parameters']={name:{k:(v if v is None or isinstance(v,(str,bool,int)) or isinstance(v,float) and np.isfinite(v) else repr(v)) for k,v in make_estimator(name).get_params(deep=False).items()} for name in MODELS}
if (RESULTS/'protocol.json').exists(): assert json.loads((RESULTS/'protocol.json').read_text())==SIGNATURE
else: save('protocol.json',SIGNATURE)

def confusion_figure(row,title,filename):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    counts=np.array([[row['tn'],row['fp']],[row['fn'],row['tp']]])
    fig,ax=plt.subplots(figsize=(5,4));ax.imshow(np.maximum(counts,1),norm=LogNorm(vmin=1,vmax=max(2,counts.max())),cmap='Blues')
    for (y,x),value in np.ndenumerate(counts):ax.text(x,y,f'{value:,}',ha='center',va='center',color='white' if value>np.sqrt(max(2,counts.max())) else 'black')
    ax.set(xticks=[0,1],yticks=[0,1],xticklabels=['No alert','Alert'],yticklabels=['No onset','Synthetic onset'],xlabel='Prediction',ylabel='Target',title=title)
    fig.tight_layout();(RESULTS/'figures').mkdir(exist_ok=True);fig.savefig(RESULTS/'figures'/filename,dpi=130);plt.close(fig)

def frame(path,full=True):
    columns=['prediction_time','barangay_psgc','municipality_psgc','is_eligible','split','outage_next_hour']+(BASE if full else [])
    f=pq.ParquetFile(path).read(columns=columns).to_pandas()
    f=f.loc[f.is_eligible].copy()
    t=pd.to_datetime(f.prediction_time)
    expected=np.where(t<pd.Timestamp(SPLITS['validation']['cutoff_start_inclusive']),'train',np.where(t<pd.Timestamp(SPLITS['test']['cutoff_start_inclusive']),'validation','test'))
    assert np.array_equal(f.split.to_numpy(),expected)
    return f

COUNTS={s:{'rows':0,'positives':0} for s in ['train','validation','test']}
STRATA=[]
for path in FILES:
    f=frame(path,False)
    for split,g in f.groupby('split'):
        COUNTS[split]['rows']+=len(g);COUNTS[split]['positives']+=int(g.outage_next_hour.sum())
    train=f.loc[f.split=='train']
    for code,g in train.loc[train.outage_next_hour==0].groupby('municipality_psgc'):
        STRATA.append({'path':str(path.relative_to(LOCAL)),'municipality_psgc':code,'negatives':len(g)})
save('population_counts.json',COUNTS)
print('Populations',COUNTS,flush=True)

def matrix(f):
    ids=f.municipality_psgc.to_numpy()
    encoded=np.column_stack([ids==p for p in MUNICIPALITIES]).astype('float32')
    assert (encoded.sum(axis=1)==1).all()
    return np.column_stack([f[BASE].to_numpy('float32'),encoded])

def materialize(split):
    x=np.lib.format.open_memmap(LOCAL/f'{split}_raw.npy',mode='w+',dtype='float32',shape=(COUNTS[split]['rows'],len(FEATURES)))
    y=np.lib.format.open_memmap(LOCAL/f'{split}_y.npy',mode='w+',dtype='uint8',shape=(len(x),))
    offset=0
    for path in FILES:
        year=int(path.parent.parent.name.split('=')[1])
        if (split=='train' and year>=2024) or (split=='validation' and year!=2024) or (split=='test' and year<2025): continue
        f=frame(path);f=f.loc[f.split==split]
        n=len(f);x[offset:offset+n]=matrix(f);y[offset:offset+n]=f.outage_next_hour.to_numpy('uint8');offset+=n
    assert offset==len(x);x.flush();y.flush()
    return x,y

def sample(negative_size):
    path=RESULTS/f'shared_sample_{negative_size}.npz'
    if path.exists():
        data=np.load(path);return data['x'],data['y']
    quota=pd.DataFrame(STRATA)
    allocation=negative_size*quota.negatives/quota.negatives.sum()
    quota['n']=np.floor(allocation).astype(int)
    left=negative_size-int(quota.n.sum())
    quota.loc[(allocation-quota.n).sort_values(ascending=False,kind='stable').head(left).index,'n']+=1
    assert (quota.n<=quota.negatives).all()
    rows=[]
    for path_name,g in quota.groupby('path',sort=True):
        f=frame(LOCAL/path_name);f=f.loc[f.split=='train']
        rows.append(f.loc[f.outage_next_hour==1])
        for r in g.itertuples():
            pool=f.loc[(f.outage_next_hour==0)&(f.municipality_psgc==r.municipality_psgc)]
            seed=int(hashlib.sha256(f'{SEED}:{path_name}:{r.municipality_psgc}'.encode()).hexdigest()[:8],16)
            rows.append(pool.sample(n=r.n,random_state=seed))
    selected=pd.concat(rows,ignore_index=True).sample(frac=1,random_state=SEED).reset_index(drop=True)
    assert not selected.duplicated(['prediction_time','barangay_psgc']).any()
    x,y=matrix(selected),selected.outage_next_hour.to_numpy('uint8')
    assert int(y.sum())==COUNTS['train']['positives'] and int((y==0).sum())==negative_size
    np.savez_compressed(RESULTS/f'shared_sample_{negative_size}.npz',x=x,y=y)
    selected[['prediction_time','barangay_psgc','municipality_psgc']].to_csv(RESULTS/f'shared_sample_{negative_size}_keys.csv',index=False)
    quota.to_csv(RESULTS/f'sampling_quotas_{negative_size}.csv',index=False)
    return x,y

def preprocess(x):
    imputer=SimpleImputer(strategy='median',keep_empty_features=True)
    clean=imputer.fit_transform(x).astype('float32')
    scaler=StandardScaler().fit(clean)
    return imputer,scaler,clean

def inputs(name,clean,scaler):
    return scaler.transform(clean).astype('float32') if name=='Logistic Regression' else clean

def predict(bundle,x):
    result=np.lib.format.open_memmap(LOCAL/'scores.npy',mode='w+',dtype='float32',shape=(len(x),))
    for lo in range(0,len(x),100000):
        clean=bundle['imputer'].transform(x[lo:lo+100000]).astype('float32')
        result[lo:lo+len(clean)]=bundle['model'].predict_proba(inputs(bundle['name'],clean,bundle['scaler']))[:,1]
    return np.asarray(result).copy()

def metrics(y,p,threshold=None,budget=1):
    if threshold is None:
        order=np.argsort(-p,kind='stable');scores=p[order]
        ends=np.r_[np.flatnonzero(scores[:-1]!=scores[1:]),len(scores)-1]
        allowed=ends[ends+1<=int(len(y)*budget/1000)]
        threshold=float(scores[allowed[-1]]) if len(allowed) else float(np.nextafter(scores[0],np.float32(np.inf)))
    flagged=p>=threshold;tp=int(y[flagged].sum());fp=int(flagged.sum())-tp;positives=int(y.sum())
    return {'average_precision':float(average_precision_score(y,p)),'roc_auc':float(roc_auc_score(y,p)),
        'brier':float(brier_score_loss(y,p)),'threshold':threshold,'tp':tp,'fp':fp,'fn':positives-tp,'tn':len(y)-positives-fp,
        'precision':tp/max(1,int(flagged.sum())),'recall':tp/positives,'false_alerts_per_1000':fp/len(y)*1000,
        'rows':len(y),'positives':positives,'prevalence':positives/len(y),'score_max':float(p.max()),
        'positive_score_median':float(np.median(p[y==1]))}

def control_scores(x):
    directory=Path('/content/drive/MyDrive/PENELCO_ML_V2/training_outputs/reviewed_v1/serving_export_v1')
    if not (directory/'manifest.json').is_file():return None
    m=json.loads((directory/'manifest.json').read_text())
    assert m['features']==BASE and hashlib.sha256((directory/'model.ubj').read_bytes()).hexdigest()==m['model_sha256']
    booster=xgboost.Booster(model_file=directory/'model.ubj');booster.set_param({'nthread':2})
    result=np.empty(len(x),dtype='float32');median=np.asarray(m['imputer_medians'],dtype='float32')
    for lo in range(0,len(x),100000):
        a=np.asarray(x[lo:lo+100000,:24]);a=np.where(np.isnan(a),median,a).astype('float32')
        result[lo:lo+len(a)]=booster.inplace_predict(a)
    return result

vx,vy=materialize('validation')

def compare(names,negative_size,label):
    sx,sy=sample(negative_size);imputer,scaler,clean=preprocess(sx);del sx
    report=[]
    for name in names:
        checkpoint=RESULTS/f'{label}_{name.replace(" ","_")}.joblib'
        meta_path=checkpoint.with_suffix('.json')
        if checkpoint.exists() and meta_path.exists():
            row=json.loads(meta_path.read_text());assert row['dataset_id']==DATA_ID;report.append(row);continue
        started=time.time();model=make_estimator(name)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always');model.fit(inputs(name,clean,scaler),sy)
        bundle={'name':name,'model':model,'imputer':imputer,'scaler':scaler,'features':FEATURES,'municipality_categories':MUNICIPALITIES,'dataset_id':DATA_ID}
        p=predict(bundle,vx);row={'model':name,'dataset_id':DATA_ID,'negative_sample_size':negative_size,'fit_seconds':time.time()-started,
            'warnings':[str(w.message) for w in caught],**metrics(vy,p)}
        joblib.dump(bundle,checkpoint);meta_path.write_text(json.dumps(row,indent=2));report.append(row)
        confusion_figure(row,f'{name} / {label} validation',f'{label}_{name.replace(" ","_")}.png')
        pd.DataFrame(report).sort_values('average_precision',ascending=False).to_csv(RESULTS/f'{label}.csv',index=False)
        print(label,json.dumps(row),flush=True)
        del bundle,model,p;gc.collect()
    pd.DataFrame(report).sort_values('average_precision',ascending=False).to_csv(RESULTS/f'{label}.csv',index=False)
    del clean,sy;gc.collect()
    return sorted(report,key=lambda r:r['average_precision'],reverse=True)

benchmark=compare(MODELS,400000,'benchmark')
print('Seven-model ranking',[(r['model'],r['average_precision']) for r in benchmark],flush=True)
confirmation=compare([r['model'] for r in benchmark[:2]],1200000,'confirmation')
selected=confirmation[0]['model']
selection={'name':selected,'dataset_id':DATA_ID,'confirmation':confirmation,'benchmark':benchmark,'protocol':SIGNATURE}
if (RESULTS/'selection.json').exists(): assert json.loads((RESULTS/'selection.json').read_text())==selection
else: save('selection.json',selection)
print('Frozen finalist',selected,flush=True)

checkpoint=RESULTS/'full_training_bundle.joblib'
if not checkpoint.exists():
    print('Full population training',COUNTS['train'],flush=True)
    tx,ty=materialize('train');imputer,scaler,clean=preprocess(tx)
    started=time.time();model=make_estimator(selected)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always');model.fit(inputs(selected,clean,scaler),ty)
    bundle={'name':selected,'model':model,'imputer':imputer,'scaler':scaler,'features':FEATURES,
        'municipality_categories':MUNICIPALITIES,'dataset_id':DATA_ID,'training_rows':len(ty),'training_positives':int(ty.sum()),
        'fit_seconds':time.time()-started,'warnings':[str(w.message) for w in caught]}
    joblib.dump(bundle,checkpoint)
    del tx,ty,clean,model;gc.collect()
else: bundle=joblib.load(checkpoint)
assert bundle['name']==selected and bundle['dataset_id']==DATA_ID

if not (RESULTS/'final_metrics.json').exists():
    vp=predict(bundle,vx);validation=metrics(vy,vp)
    old_vp=control_scores(vx);old_validation=metrics(vy,old_vp) if old_vp is not None else None
    save('frozen_threshold.json',{'name':selected,'dataset_id':DATA_ID,'validation':validation})
    print('Final validation',validation,flush=True)
    tx,ty=materialize('test');tp=predict(bundle,tx);old_tp=control_scores(tx)
    final={'name':selected,'dataset_id':DATA_ID,'validation':validation,'test':metrics(ty,tp,validation['threshold']),
        'training_rows':bundle['training_rows'],'training_positives':bundle['training_positives'],'fit_seconds':bundle['fit_seconds'],
        'frozen_v2_control':{'validation':old_validation,'test':metrics(ty,old_tp,old_validation['threshold'])} if old_validation is not None and old_tp is not None else {'status':'original V2 serving artifact unavailable'},
        'limitations':'Synthetic origin-onset targets, retrospective weather availability and annual NDVI omitted. No operational or calibrated real-outage probability claim.'}
    save('final_metrics.json',final)
    np.savez_compressed(RESULTS/'frozen_test_predictions.npz',y=ty,scores=tp)
    confusion_figure(final['test'],f'{selected} / frozen test threshold','final_test_confusion.png')
    from sklearn.metrics import precision_recall_curve
    import matplotlib.pyplot as plt
    precision,recall,_=precision_recall_curve(ty,tp)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for ax in axes:
        ax.plot(recall,precision,label='Frozen model');ax.axhline(ty.mean(),ls='--',color='gray',label='Prevalence')
        ax.set(xlabel='Recall',ylabel='Precision',xlim=(0,1));ax.legend()
    axes[0].set_ylim(0,1);axes[0].set_title('Full scale')
    axes[1].set_ylim(0,.02);axes[1].set_title('Precision zoom: 0–2%')
    fig.suptitle('Synthetic-onset test precision–recall');fig.tight_layout();fig.savefig(RESULTS/'figures/final_test_pr.png',dpi=130);plt.close(fig)
    del tx,ty,tp;gc.collect()
final=json.loads((RESULTS/'final_metrics.json').read_text())
print('FINAL',json.dumps(final),flush=True)

# All 12 categories plus missing-base-feature cases for independent serving parity.
"""Export fitted sklearn numerical models without serializing executable objects."""
def portable_specification(bundle):
    import numpy as np
    model=bundle['model'];name=bundle['name']
    if name=='Logistic Regression':
        return {'kind':'linear_logistic','coefficients':model.coef_[0].tolist(),'intercept':float(model.intercept_[0]),'scaler_mean':bundle['scaler'].mean_.tolist(),'scaler_scale':bundle['scaler'].scale_.tolist()}
    def standard_tree(estimator):
        tree=estimator.tree_;counts=tree.value[:,0,:];probability=counts[:,1]/counts.sum(axis=1)
        return {'left':tree.children_left.tolist(),'right':tree.children_right.tolist(),'feature':tree.feature.tolist(),'threshold':tree.threshold.tolist(),'value':probability.tolist()}
    if name in ['Decision Tree','Random Forest','Extra Trees','AdaBoost']:
        estimators=[model] if name=='Decision Tree' else model.estimators_
        result={'kind':'adaboost_samme' if name=='AdaBoost' else 'tree_average','trees':[standard_tree(e) for e in estimators]}
        if name=='AdaBoost':result['weights']=model.estimator_weights_[:len(estimators)].tolist()
        return result
    if name=='Histogram Gradient Boosting':
        trees=[]
        for iteration in model._predictors:
            assert len(iteration)==1
            nodes=iteration[0].nodes;leaf=nodes['is_leaf'].astype(bool)
            assert not nodes['is_categorical'].any()
            trees.append({'left':np.where(leaf,-1,nodes['left'].astype('int64')).tolist(),'right':np.where(leaf,-1,nodes['right'].astype('int64')).tolist(),'feature':nodes['feature_idx'].tolist(),'threshold':nodes['num_threshold'].tolist(),'value':nodes['value'].tolist()})
        return {'kind':'hist_gradient_boosting','intercept':float(model._baseline_prediction[0,0]),'trees':trees}
    raise ValueError('Unsupported portable export: '+name)

raw=np.concatenate([vx[(lambda idx: idx[np.linspace(0,len(idx)-1,12,dtype=int)])(np.flatnonzero(vx[:,24+i]==1))] for i in range(12)])
extra=raw[:9].copy()
for i in range(9):extra[i,i]=np.nan
raw=np.concatenate([raw,extra])
expected=predict(bundle,raw)
save('parity.json',{'inputs':[[None if np.isnan(v) else float(v) for v in row] for row in raw],'expected_scores':expected.tolist()})
if selected=='XGBoost':
    bundle['model'].get_booster().save_model(RESULTS/'model.ubj');artifact='model.ubj';model_type='xgboost_binary_logistic'
else:
    save('model.json',portable_specification(bundle));artifact='model.json';model_type='portable_binary_classifier'
model_sha=hashlib.sha256((RESULTS/artifact).read_bytes()).hexdigest()
manifest={'artifact_version':2,'dataset_version':'3.0.0','dataset_id':DATA_ID,'event_source_sha256':META['sources']['events']['sha256'],
    'model_id':hashlib.sha256((model_sha+DATA_ID+json.dumps(FEATURES)).encode()).hexdigest(),
    'model_type':model_type,'algorithm':selected,'features':FEATURES,'base_features':BASE,'municipality_categories':MUNICIPALITIES,
    'imputer_medians':bundle['imputer'].statistics_.tolist(),'target':'outage_next_hour','target_interval':'(t,t+1h]','weather_lag_hours':1,
    'training_rows':bundle['training_rows'],'training_positives':bundle['training_positives'],
    'model_sha256':model_sha,'parity_sha256':hashlib.sha256((RESULTS/'parity.json').read_bytes()).hexdigest(),
    'reference_threshold':final['validation']['threshold'],'validation_metrics':final['validation'],'held_out_metrics':final['test'],
    'frozen_v2_control':final['frozen_v2_control'],'forecast_weather_validated':False,'software':{'python':platform.python_version(),'numpy':np.__version__,'sklearn':sklearn.__version__,'xgboost':xgboost.__version__},
    'model_artifact':artifact,'model_provenance':selection,'warnings':bundle['warnings']}
if selected=='Logistic Regression':
    manifest['scaler_mean']=bundle['scaler'].mean_.tolist();manifest['scaler_scale']=bundle['scaler'].scale_.tolist()
save('manifest.json',manifest)
archive=RESULTS/'v3_serving_export.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for name in ['manifest.json',artifact,'parity.json']:z.write(RESULTS/name,name)
print('Serving export ready',archive,flush=True)
