"""Read-only score/calibration review after model selection and held-out evaluation."""
from pathlib import Path
import argparse,csv,json
import numpy as np


def summarize_scores(y,scores):
    y=np.asarray(y);scores=np.asarray(scores,dtype='float64')
    if y.ndim!=1 or scores.shape!=y.shape or not len(y) or not np.isfinite(scores).all() or ((scores<0)|(scores>1)).any() or not np.isin(y,[0,1]).all():
        raise ValueError('Invalid binary targets or model scores')
    y=y.astype('uint8')
    edges=np.unique(np.quantile(scores,np.linspace(0,1,11)))
    bins=np.searchsorted(edges[1:-1],scores,side='right')
    rows=[]
    for index in np.unique(bins):
        mask=bins==index
        rows.append({'bin':int(index)+1,'rows':int(mask.sum()),'positives':int(y[mask].sum()),
                     'mean_score':float(scores[mask].mean()),'observed_onset_rate':float(y[mask].mean()),
                     'score_min':float(scores[mask].min()),'score_max':float(scores[mask].max())})
    summary={'rows':len(y),'positives':int(y.sum()),'observed_onset_rate':float(y.mean()),
             'score_min':float(scores.min()),'score_median':float(np.median(scores)),
             'score_mean':float(scores.mean()),'score_max':float(scores.max()),
             'brier':float(np.mean((scores-y)**2)),'constant_prevalence_brier':float(y.mean()*(1-y.mean()))}
    return summary,rows


def review(results,baseline_scores=None):
    results=Path(results)
    final=json.loads((results/'final_metrics.json').read_text())
    with np.load(results/'frozen_test_predictions.npz') as saved:y,p=saved['y'],saved['scores']
    summary,bins=summarize_scores(y,p)
    if summary['rows']!=final['test']['rows'] or summary['positives']!=final['test']['positives']:
        raise ValueError('Predictions do not match frozen evaluation')
    summary={'algorithm':final['name'],'dataset_id':final['dataset_id'],'candidate':summary,
             'frozen_v2_control_metrics':final.get('frozen_v2_control'),
             'interpretation':'Raw classifier outputs; calibration has not been validated. No prediction is adjusted by this review.'}
    if baseline_scores is not None:
        summary['frozen_v2_control_scores'],_=summarize_scores(y,baseline_scores)
    (results/'calibration_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    with (results/'test_score_deciles.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(bins[0]));writer.writeheader();writer.writerows(bins)
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    means=[row['mean_score'] for row in bins];rates=[row['observed_onset_rate'] for row in bins]
    axes[0].plot([0,1],[0,1],ls='--',color='gray',label='Ideal calibration')
    axes[0].plot(means,rates,'o-',label='Held-out score bins')
    axes[0].set(xlabel='Mean raw model score',ylabel='Observed synthetic onset rate',xlim=(0,1),ylim=(0,1),title='Probability calibration');axes[0].legend()
    axes[1].plot(range(1,len(bins)+1),rates,'o-',label='Observed onset rate')
    axes[1].axhline(y.mean(),ls='--',color='gray',label='Test prevalence')
    axes[1].set(xlabel='Score bin (ascending; tied values kept together)',ylabel='Observed synthetic onset rate',title='Ranking across score bins');axes[1].legend()
    fig.suptitle(final['name']+' — frozen V3 test predictions');fig.tight_layout()
    (results/'figures').mkdir(exist_ok=True);fig.savefig(results/'figures/final_test_calibration.png',dpi=130);plt.close(fig)
    print(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results',type=Path)
    review(parser.parse_args().results)
