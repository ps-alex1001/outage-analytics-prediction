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
