"""Numeric, read-only inference for an explicitly validated binary classifier export."""
from __future__ import annotations
import numpy as np


def sigmoid(values):
    values=np.asarray(values,dtype='float64')
    result=np.empty_like(values)
    positive=values>=0
    result[positive]=1/(1+np.exp(-values[positive]))
    exp=np.exp(values[~positive]);result[~positive]=exp/(1+exp)
    return result


class PortableClassifier:
    def __init__(self, specification, feature_count):
        self.specification=specification
        self.kind=specification['kind']
        if self.kind not in {'linear_logistic','tree_average','adaboost_samme','hist_gradient_boosting'}:
            raise ValueError('Unsupported portable classifier')
        if self.kind=='linear_logistic':
            self.coef=np.asarray(specification['coefficients'],dtype='float64')
            self.intercept=float(specification['intercept'])
            self.mean=np.asarray(specification['scaler_mean'],dtype='float64')
            self.scale=np.asarray(specification['scaler_scale'],dtype='float64')
            if any(a.shape!=(feature_count,) or not np.isfinite(a).all() for a in [self.coef,self.mean,self.scale]) or (self.scale<=0).any() or not np.isfinite(self.intercept):
                raise ValueError('Invalid linear parameters')
            return
        self.trees=[]
        for tree in specification['trees']:
            values={key:np.asarray(tree[key],dtype=dtype) for key,dtype in [('left','int64'),('right','int64'),('feature','int64'),('threshold','float64'),('value','float64')]}
            n=len(values['left'])
            if n<1 or any(v.shape!=(n,) for v in values.values()):raise ValueError('Invalid tree dimensions')
            leaves=values['left']==-1;branch=~leaves;indices=np.arange(n)
            if not np.isfinite(values['value']).all() or not np.isfinite(values['threshold'][branch]).all():raise ValueError('Nonfinite tree parameters')
            if (values['right'][leaves]!=-1).any() or (values['left'][branch]<=indices[branch]).any() or (values['right'][branch]<=indices[branch]).any() or (values['left'][branch]>=n).any() or (values['right'][branch]>=n).any():raise ValueError('Invalid forward tree edges')
            if ((values['feature'][branch]<0)|(values['feature'][branch]>=feature_count)).any():raise ValueError('Invalid tree feature')
            if self.kind!='hist_gradient_boosting' and ((values['value'][leaves]<0)|(values['value'][leaves]>1)).any():raise ValueError('Invalid leaf probability')
            self.trees.append(values)
        if not self.trees:raise ValueError('Empty ensemble')
        if self.kind=='adaboost_samme':
            self.weights=np.asarray(specification['weights'],dtype='float64')
            if self.weights.shape!=(len(self.trees),) or not np.isfinite(self.weights).all() or (self.weights<0).any() or self.weights.sum()<=0:raise ValueError('Invalid ensemble weights')
        if self.kind=='hist_gradient_boosting':
            self.intercept=float(specification['intercept'])
            if not np.isfinite(self.intercept):raise ValueError('Invalid boosting intercept')

    @staticmethod
    def tree_values(tree,x):
        nodes=np.zeros(len(x),dtype='int64')
        branch=tree['left'][nodes]!=-1
        while branch.any():
            rows=np.flatnonzero(branch);current=nodes[rows]
            go_left=x[rows,tree['feature'][current]]<=tree['threshold'][current]
            nodes[rows]=np.where(go_left,tree['left'][current],tree['right'][current])
            branch=tree['left'][nodes]!=-1
        return tree['value'][nodes]

    def predict(self,values):
        # sklearn tree classifiers cast raw input to float32. HGB uses float64.
        x=np.asarray(values,dtype='float64' if self.kind=='hist_gradient_boosting' else 'float32')
        if self.kind=='linear_logistic':
            # Training scaler output is explicitly float32 before LogisticRegression.
            scaled=x.copy(); scaled-=self.mean; scaled/=self.scale
            return sigmoid(scaled@self.coef+self.intercept)
        result=np.zeros(len(x),dtype='float64')
        for i,tree in enumerate(self.trees):
            leaf=self.tree_values(tree,x)
            if self.kind=='adaboost_samme':result+=np.where(leaf>.5,1.,-1.)*self.weights[i]
            else:result+=leaf
        if self.kind=='adaboost_samme':return sigmoid(2*result/self.weights.sum())
        if self.kind=='hist_gradient_boosting':return sigmoid(result+self.intercept)
        return result/len(self.trees)
