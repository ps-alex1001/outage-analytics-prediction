"""Chronological external-memory XGBoost experiment; never changes live artifacts."""
from __future__ import annotations
import gc
import hashlib
import json
import platform
from pathlib import Path
import sys
import time

import numpy as np
import pyarrow.parquet as pq
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.forecast_features import FEATURES
from backend.forecast_model import ForecastModel

OUT = ROOT / 'data/models/municipality_experiment_v1'
OUT.mkdir(parents=True, exist_ok=True)
CACHE = OUT / 'external_cache'
CACHE.mkdir(exist_ok=True)
OLD = ROOT / 'data/models/outage_v2_xgboost'
MANIFEST = json.loads((OLD / 'manifest.json').read_text())
MEDIANS = np.asarray(MANIFEST['imputer_medians'], dtype='float32')
MUNICIPALITIES = sorted({str(v) for v in pq.ParquetFile(ROOT / 'data/ml/v2/ml_dataset_v2/year=2024/month=01/part-000.parquet').read(columns=['municipality_psgc']).column(0).to_pylist()})
INPUTS = FEATURES + ['municipality_' + m for m in MUNICIPALITIES]
COLUMNS = FEATURES + ['municipality_psgc', 'outage_next_hour', 'is_eligible']
FILES = sorted((ROOT / 'data/ml/v2/ml_dataset_v2').glob('year=*/month=*/part-000.parquet'))


def batches(split):
    for path in FILES:
        year = int(path.parent.parent.name.split('=')[1])
        if not ((split == 'train' and year < 2024) or (split == 'validation' and year == 2024) or (split == 'test' and year >= 2025)):
            continue
        for rb in pq.ParquetFile(path).iter_batches(columns=COLUMNS, batch_size=150000):
            f = rb.to_pandas()
            f = f.loc[f.is_eligible]
            if f.empty: continue
            base = f[FEATURES].to_numpy('float32')
            base = np.where(np.isnan(base), MEDIANS, base)
            ids = f.municipality_psgc.to_numpy()
            encoded = np.column_stack([ids == m for m in MUNICIPALITIES]).astype('float32')
            if not (encoded.sum(axis=1) == 1).all(): raise ValueError('Unknown municipality')
            yield np.column_stack([base, encoded]), f.outage_next_hour.to_numpy('float32')


class TrainingIter(xgb.DataIter):
    def __init__(self):
        self.iterator = None
        super().__init__(cache_prefix=str(CACHE / 'train').replace('\\', '/'))
    def reset(self): self.iterator = iter(batches('train'))
    def next(self, input_data):
        item = next(self.iterator, None)
        if item is None: return False
        x, y = item
        input_data(data=x, label=y)
        return True


def metrics(y, p, threshold=None):
    order = np.argsort(-p, kind='stable'); s, labels = p[order], y[order]
    ends = np.r_[np.flatnonzero(s[:-1] != s[1:]), len(s)-1]
    tp = np.cumsum(labels, dtype='float64')[ends]
    n = ends + 1; fp = n - tp
    positives, negatives = int(y.sum()), int(len(y)-y.sum())
    ap = float(np.sum((tp / n) * np.diff(np.r_[0, tp])) / positives)
    auc = float(np.trapezoid(np.r_[0,tp/positives], np.r_[0,fp/negatives]))
    if threshold is None:
        allowed = ends[n <= int(len(y) / 1000)]
        threshold = float(s[allowed[-1]]) if len(allowed) else float(np.nextafter(s[0], np.float32(np.inf)))
    detected = p >= threshold
    true = int(y[detected].sum()); false = int(detected.sum()) - true
    return {'average_precision':ap, 'roc_auc':auc, 'threshold':threshold,
        'recall':true/positives, 'precision':true/max(1,int(detected.sum())), 'tp':true,'fp':false,
        'fn':positives-true,'tn':negatives-false,'false_alerts_per_1000':false/len(y)*1000,
        'brier':float(np.mean((p.astype('float64')-y)**2)), 'rows':len(y),'positives':positives,
        'score_min':float(p.min()),'score_max':float(p.max()),'score_median':float(np.median(p)),
        'positive_score_median':float(np.median(p[y == 1]))}


def save(name, value):
    (OUT/name).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def main():
    if (OUT/'frozen_selection.json').exists():
        raise RuntimeError('Experiment already frozen; use a new version for a new experiment')
    plan = {'features':INPUTS, 'municipality_encoding':'one-hot exact PSGC strings',
        'train':'2019–2023 all eligible rows', 'validation':'2024 full unsampled population',
        'test':'2025–June 2026 previously evaluated test, reused comparison; not fresh independent confirmation',
        'seed':42, 'selection':'validation AP; require AP improvement and no recall regression at <=1 alert/1000 validation rows',
        'configs':[{'max_depth':3,'min_child_weight':30}, {'max_depth':5,'min_child_weight':20}, {'max_depth':4,'min_child_weight':50}],
        'rounds':400,'early_stopping':35, 'no_score_inflation':True}
    save('plan.json',plan)
    print('Building disk-backed training matrix for all 10,368,667 eligible rows',flush=True)
    iterator = TrainingIter()
    train = xgb.ExtMemQuantileDMatrix(iterator, max_bin=64, nthread=2)
    assert train.num_row() == 10368667
    print('Loading full 2024 validation only',flush=True)
    pairs = list(batches('validation'))
    vx = np.concatenate([x for x,y in pairs]); vy = np.concatenate([y for x,y in pairs]); del pairs
    val = xgb.QuantileDMatrix(vx, label=vy, max_bin=64, ref=train, nthread=2)
    old = ForecastModel(OLD)
    baseline = metrics(vy, old.predict(vx[:,:24]))
    save('baseline_validation.json',baseline)
    print('Baseline validation '+json.dumps(baseline),flush=True)
    results=[]
    for i, config in enumerate(plan['configs']):
        params={'objective':'binary:logistic','tree_method':'hist','max_bin':64,'nthread':2,
            'eta':.05,'subsample':.8,'colsample_bytree':1.,'lambda':5.,'seed':42,'eval_metric':'aucpr',**config}
        started=time.time()
        model=xgb.train(params,train,num_boost_round=400,evals=[(val,'validation')],
            early_stopping_rounds=35,verbose_eval=25)
        model=model[:model.best_iteration+1]
        p=model.inplace_predict(vx)
        result={'candidate':i,'params':params,'rounds':model.num_boosted_rounds(),'seconds':time.time()-started,
                'validation':metrics(vy,p)}
        model.save_model(OUT/f'candidate_{i}.ubj'); results.append(result); save('candidates.json',results)
        print('CANDIDATE '+json.dumps(result),flush=True)
    eligible=[r for r in results if r['validation']['average_precision'] > baseline['average_precision']
              and r['validation']['recall'] >= baseline['recall']]
    if not eligible:
        save('no_promotion.json',{'reason':'No candidate passed the predefined validation gate','baseline':baseline})
        print('No candidate passed; no deployment or test selection',flush=True);return
    best=max(eligible,key=lambda r:r['validation']['average_precision'])
    save('frozen_selection.json',best)
    model=xgb.Booster(model_file=OUT/f'candidate_{best["candidate"]}.ubj');model.set_param({'nthread':2})
    sample=np.concatenate([vx[np.flatnonzero(vx[:,24+i] == 1)[:12]] for i in range(len(MUNICIPALITIES))])
    extra=sample[:9].copy()
    for i in range(9): extra[i,i]=np.nan
    sample=np.concatenate([sample,extra])
    del vx,vy,val,train,iterator;gc.collect()
    print('Selection frozen. Evaluating previously used test period once.',flush=True)
    ys,ps,bs=[],[],[]
    for x,y in batches('test'):
        ys.append(y);ps.append(model.inplace_predict(x));bs.append(old.predict(x[:,:24]))
    y,p,b=np.concatenate(ys),np.concatenate(ps),np.concatenate(bs)
    comparison={'candidate':metrics(y,p,best['validation']['threshold']),
                'baseline':metrics(y,b,baseline['threshold']),
                'caveat':plan['test']}
    save('test_comparison.json',comparison)
    print('TEST '+json.dumps(comparison),flush=True)
    # Self-contained native serving export, versioned and separate from live model.
    model.save_model(OUT/'model.ubj')
    all_medians=np.r_[MEDIANS,np.zeros(len(MUNICIPALITIES),dtype='float32')]
    expected=model.inplace_predict(np.where(np.isnan(sample),all_medians,sample).astype('float32'))
    parity={'inputs':[[None if np.isnan(v) else float(v) for v in row] for row in sample],
            'expected_scores':expected.tolist()}
    save('parity.json',parity)
    manifest={**MANIFEST,'artifact_version':2,'features':INPUTS,'base_features':FEATURES,
        'municipality_categories':MUNICIPALITIES,'imputer_medians':MEDIANS.tolist()+[0.]*len(MUNICIPALITIES),
        'reference_threshold':best['validation']['threshold'],'held_out_metrics':comparison['candidate'],
        'validation_metrics':best['validation'],'comparison':comparison,'experiment':plan,
        'parent_model_id':MANIFEST['model_id'],
        'software':{'python':platform.python_version(),'xgboost':xgb.__version__,'numpy':np.__version__},
        'model_sha256':hashlib.sha256((OUT/'model.ubj').read_bytes()).hexdigest(),
        'parity_sha256':hashlib.sha256((OUT/'parity.json').read_bytes()).hexdigest()}
    manifest['model_id']=hashlib.sha256(json.dumps([manifest['model_sha256'],INPUTS],sort_keys=True).encode()).hexdigest()
    save('manifest.json',manifest)
    print('Replacement bundle exported '+str(OUT),flush=True)


if __name__=='__main__':main()
