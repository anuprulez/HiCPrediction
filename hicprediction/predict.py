#!/usr/bin/env python3
import os
os.environ['NUMEXPR_MAX_THREADS'] = '16'
os.environ['NUMEXPR_NUM_THREADS'] = '8'
import hicprediction.configurations as conf
import click
from hicprediction.tagCreator import createPredictionTag
import joblib
import pandas as pd
import numpy as np
#import h5py
from scipy import sparse
from scipy import ndimage
from hicmatrix import HiCMatrix as hm
import sklearn.metrics as metrics
import sys

"""
Module responsible for the prediction of test set, their evaluation and the
conversion of prediction to HiC matrices
"""

@conf.predict_options
@click.command()
def executePredictionWrapper(modelfilepath, basefile, predictionsetpath,
                      predictionoutputdirectory, resultsfilepath, internalindir, sigma):
    """
    Wrapper function for Cli
    """

    if not conf.checkExtension(modelfilepath, '.z'):
        msg = "model file {0:s} does not have a .z file extension. Aborted"
        sys.exit(msg.format(modelfilepath))
    if not conf.checkExtension(predictionsetpath, '.z'):
        msg = "prediction file {0:s} does not have a .z file extension. Aborted"
        sys.exit(msg.format(predictionsetpath))

    #load trained model and testSet (target for prediction)
    try:
    model, modelParams = joblib.load(modelfilepath)
    testSet, setParams = joblib.load(predictionsetpath)
    except Exception as e:
        print(e)
        msg = "Failed loading model and test set. Wrong format?"
        sys.exit(msg)

def executePrediction(model,modelParams, basefile, testSet, setParams,
                      predictionoutputdirectory, resultsfilepath, internalInDir, sigma):
    """ 
    Main function
    calls prediction, evaluation and conversion methods and stores everything
    Attributes:
        model -- regression model
        modelParams -- parameters of model and set the model was trained with
        basefile-- path to basefile of test set
        predictionsetpath -- path to data set that is to be predicted
        predictionoutputdirectory -- path to store prediction
        resultsfilepath --  path to results file for evaluation storage
    """
    ### check extensions
    if not conf.checkExtension(basefile, '.ph5'):
        msg = "basefile {0:s} must have a .ph5 file extension"
        sys.exit(msg.format(basefile))
    #check if the test set is a compound dataset (e.g. concatenated from diverse sets). 
    #this is not allowed for now
    if isinstance(setParams["chrom"], list) or isinstance(setParams["cellType"], list):
        msg = "The target dataset is a compound (concatenated) dataset with multiple chromosomes"
        msg += "or cell lines.\n" 
        msg += "Compound datasets cannot be predicted. Aborting"
        sys.exit(msg) 
    
    #prepare dataframe for prediction results
    df = None
    if resultsfilepath:
        if not conf.checkExtension(resultsfilepath, '.csv'):
            resultsfilename = os.path.splitext(resultsfilepath)[0]
            resultsfilepath = resultsfilename + ".csv"
            msg = "result file must have .csv file extension"
            msg += "renamed file to {0:s}"
            print(msg.format(resultsfilepath))
        columns = ['Score', 
                    'R2',
                    'MSE', 
                    'MAE', 
                    'MSLE',
                    'AUC_OP_S',
                    'AUC_OP_P', 
                    'S_OP', 
                    'S_OA', 
                    'S_PA',
                    'P_OP',
                    'P_OA',
                    'P_PA',
                    'Window', 
                    'Merge',
                    'normalize',
                    'ignoreCentromeres',
                    'conversion', 
                    'Loss', 
                    'resolution',
                    'modelChromosome', 
                    'modelCellType',
                    'predictionChromosome', 
                    'predictionCellType']
        dists = sorted(list(testSet.distance.unique()))
        columns.extend(dists)
        columns.append('Tag')
        df = pd.DataFrame(columns=columns)
        df = df.set_index('Tag')
    
    ### predict test dataset from model
    prediction, score = predict(model, testSet, modelParams)
    
    predictionTag = createPredictionTag(modelParams, setParams)
    ### convert prediction back to matrix, if output path set
    if predictionoutputdirectory:
        predictionFilePath =  os.path.join(predictionoutputdirectory,predictionTag + ".cool")
        if modelParams['method'] == 'oneHot':
            convertToMatrix = predictionToMatrix2
        elif modelParams['method'] == 'multiColumn':
            convertToMatrix = predictionToMatrix
        else:
            raise NotImplementedError()
        predictionToMatrix(prediction, basefile, modelParams,\
                           setParams['chrom'], predictionFilePath, internalInDir, sigma)
    
    ### store evaluation metrics, if results path set
    if resultsfilepath:
        if score:
        df.to_csv(resultsfilepath)


def predict(model, testSet, pModelParams):
    """
    Function to predict test set
    Attributes:
        model -- model to use
        testSet -- testSet to be predicted
        conversion -- conversion function used when training the model
    """
    ### check if the test set contains reads, only then can we compute score later on
    nanreadMask = testSet['reads'] == np.nan
    testSetHasTargetValues =  testSet[nanreadMask].empty    
    
    ### Eliminate NaNs - there should be none
    nrOfRowsBefore = testSet.shape[0]
    testSet.fillna(value=0, inplace=True)
    nrOfRowsAfter = testSet.shape[0]
    if nrOfRowsAfter != nrOfRowsBefore:
        msg = "Warning: Removed {0:d} rows from test set because they contained NaNs"
        print(msg.format(nrOfRowsBefore-nrOfRowsAfter))
    
    ### Hide Columns that are not needed for prediction
    dropList = ['first', 'second', 'chrom', 'reads', 'avgRead']
    noDistance = 'noDistance' in pModelParams and pModelParams['noDistance'] == True
    noMiddle = 'noMiddle' in pModelParams and pModelParams['noMiddle'] == True
    noStartEnd = 'noStartEnd' in pModelParams and pModelParams['noStartEnd'] == True
    if noDistance:
        dropList.append('distance')
    if noMiddle:
        if pModelParams['method'] == 'oneHot':
            dropList.append('middleProt')
        elif pModelParams['method'] == 'multiColumn':
            numberOfProteins = int((testSet.shape[1] - 6) / 3)
            for protein in range(numberOfProteins):
                dropList.append(str(protein + numberOfProteins))
        else:
            raise NotImplementedError()
    if noStartEnd:
        if pModelParams['method'] == 'oneHot':
            dropList.append('startProt')
            dropList.append('endProt')
        elif pModelParams['method'] == 'multiColumn':
            numberOfProteins = int((testSet.shape[1] - 6) / 3)
            for protein in range(numberOfProteins):
                dropList.append(str(protein))
                dropList.append(str(protein + 2 * numberOfProteins))
        else:
            raise NotImplementedError()
    test_X = testSet[testSet.columns.difference(dropList)]
    test_y = testSet.copy(deep=True)
    ### convert reads to log reads
    test_y['standardLog'] = np.log(testSet['reads']+1)
    ### predict
    y_pred = model.predict(test_X)
    test_y['pred'] = y_pred
    y_pred = np.absolute(y_pred)
    test_y['predAbs'] = y_pred
    ### convert back if necessary
    if pModelParams['conversion'] == 'none':
        target = 'reads'
        reads = y_pred
    elif pModelParams['conversion'] == 'standardLog':
        target = 'standardLog'
        reads = y_pred
        reads = np.exp(reads) - 1
    ### store into new dataframe
    test_y['predReads'] = reads
    if testSetHasTargetValues:
    score = model.score(test_X,test_y[target])
    else:
        score = None
    return test_y, score

def predictionToMatrix2(pred, baseFilePath, pModelParams, chromosome, predictionFilePath, internalInDir, pSigma):

    """
    Function to convert prediction to Hi-C matrix
    Attributes:
            pred -- prediction dataframe
            baseFilePath --  base file
            conversion -- conversion technique that was used
            chromosome -- chromosome that wwas predicted
            predictionFilePath -- where to store the new matrix
    """
    with h5py.File(baseFilePath, 'r') as baseFile:
        ### store conversion function
        if pModelParams['conversion'] == "standardLog":
            convert = lambda val: np.exp(val) - 1
        elif pModelParams['conversion'] == "none":
            convert = lambda val: val
        ### get individual predictions for the counts from each protein
        resList = []
        numberOfProteins = pred.shape[1] - 13
        for protein in range(numberOfProteins):
            colName = 'prot_' + str(protein)
            mask = pred[colName] == 1
            resDf = pd.DataFrame()
            resDf['first'] = pred[mask]['first']
            resDf['second'] = pred[mask]['second']
            ### convert back
            predStr = 'pred_' + str(protein)
            resDf[predStr] = convert(pred[mask]['pred'])
            resDf.set_index(['first','second'],inplace=True)
            resList.append(resDf)
        #join the results on indices
        predictionDf = pd.DataFrame(columns=['first', 'second'])
        predictionDf.set_index(['first', 'second'], inplace=True)
        predictionDf = predictionDf.join(resList,how='outer')
        predictionDf.fillna(0.0, inplace=True)
        predictionDf['merged'] = predictionDf.mean(axis=1)
        #get the indices for the predicted counts
        predictionDf.reset_index(inplace=True)
        rows = list(predictionDf['first'])
        columns = list(predictionDf['second'])
        matIndx = (rows,columns)
        #get the predicted counts
        data = list(predictionDf['merged'])
        ### create matrix with new values and overwrite original
        matrixfile = baseFile[chromosome][()]
        if internalInDir:
            filename = os.path.basename(matrixfile)
            matrixfile = os.path.join(internalInDir, filename)
        originalMatrix = None
        if os.path.isfile(matrixfile):
            originalMatrix = hm.hiCMatrix(matrixfile)
        else:
            msg = ("cooler file {0:s} is missing.\n" \
                    + "Use --iif option to provide the directory where the internal matrices " \
                    +  "were stored when creating the basefile").format(matrixfile)
            sys.exit(msg)        
        
        predMatrix = sparse.csr_matrix((data, matIndx), shape=originalMatrix.matrix.shape)
        #smoothen the predicted matrix with a gaussian filter, if sigma > 0.0
        if pSigma > 0.0:
            upper = sparse.triu(predMatrix)
            lower = sparse.triu(predMatrix, k=1).T
            fullPredMatrix = (upper+lower).todense().astype('float32')
            filteredPredMatrix = ndimage.gaussian_filter(fullPredMatrix,pSigma)
            predMatrix = sparse.triu(filteredPredMatrix)

        originalMatrix.setMatrix(predMatrix, originalMatrix.cut_intervals)
        originalMatrix.save(predictionFilePath)

def predictionToMatrix(pred, baseFilePath, pModelParams, chromosome, predictionFilePath, internalInDir, pSigma):

    """
    Function to convert prediction to Hi-C matrix
    Attributes:
            pred -- prediction dataframe
            baseFilePath --  base file
            conversion -- conversion technique that was used
            chromosome -- chromosome that wwas predicted
            predictionFilePath -- where to store the new matrix
    """
    with h5py.File(baseFilePath, 'r') as baseFile:
        ### store conversion function
        if pModelParams['conversion'] == "standardLog":
            convert = lambda val: np.exp(val) - 1
        elif pModelParams['conversion'] == "none":
            convert = lambda val: val
        ### get rows and columns (indices) for re-building the HiC matrix
        rows = list(pred['first'])
        columns = list(pred['second'])
        matIndx = (rows,columns)
        ### convert back
        data = list(convert(pred['pred']))
        ### create matrix with new values and overwrite original
        matrixfile = baseFile[chromosome][()]
        if internalInDir:
            filename = os.path.basename(matrixfile)
            matrixfile = os.path.join(internalInDir, filename)
        originalMatrix = None
        if os.path.isfile(matrixfile):
            originalMatrix = hm.hiCMatrix(matrixfile)
        else:
            msg = ("cooler file {0:s} is missing.\n" \
                    + "Use --iif option to provide the directory where the internal matrices " \
                    +  "were stored when creating the basefile").format(matrixfile)
            sys.exit(msg)        
        
        predMatrix = sparse.csr_matrix((data, matIndx), shape=originalMatrix.matrix.shape)
        #smoothen the predicted matrix with a gaussian filter, if sigma > 0.0
        if pSigma > 0.0:
            upper = sparse.triu(predMatrix)
            lower = sparse.triu(predMatrix, k=1).T
            fullPredMatrix = (upper+lower).todense().astype('float32')
            filteredPredMatrix = ndimage.gaussian_filter(fullPredMatrix,pSigma)
            predMatrix = sparse.triu(filteredPredMatrix)

        originalMatrix.setMatrix(predMatrix, originalMatrix.cut_intervals)
        originalMatrix.save(predictionFilePath)


def getCorrelation(data, field1, field2,  resolution, method):
    """
    Helper method to calculate correlation
    """

    new = data.groupby('distance', group_keys=False)[[field1,
        field2]].corr(method=method)
    new = new.iloc[0::2,-1]
    values = new.values
    indices = new.index.tolist()
    indices = list(map(lambda x: x[0], indices))
    indices = np.array(indices)
    div = float(len(indices))
    indices = indices / div 
    return indices, values

def saveResults(pTag, df, pModelParams, pSetParams, y, pScore, pColumns):
    """
    Function to calculate metrics and store them into a file
    """
    y_pred = y['predReads']
    y_true = y['reads']
    indicesOPP, valuesOPP = getCorrelation(y,'reads', 'predReads',
                                     pModelParams['resolution'], 'pearson')
    ### calculate AUC
    aucScoreOPP = metrics.auc(indicesOPP, valuesOPP)
    corrScoreOP_P = y[['reads','predReads']].corr(method= \
                'pearson').iloc[0::2,-1].values[0]
    corrScoreOA_P = y[['reads', 'avgRead']].corr(method= \
                'pearson').iloc[0::2,-1].values[0]
    corrScoreOP_S= y[['reads','predReads']].corr(method= \
                'spearman').iloc[0::2,-1].values[0]
    corrScoreOA_S= y[['reads', 'avgRead']].corr(method= \
                'spearman').iloc[0::2,-1].values[0]
    #model parameters cell type, chromosome, window operation and merge operation may be lists
    #so generate appropriate strings for storage
    modelCellTypeList = list( np.hstack([[], pModelParams['cellType']]) )
    modelChromList = list( np.hstack([[], pModelParams['chrom']]) )
    modelWindowOpList = list( np.hstack([[], pModelParams['windowOperation']]))
    modelMergeOpList = list( np.hstack([[], pModelParams['mergeOperation']]) )
    modelCellTypeStr = ", ".join(modelCellTypeList)
    modelChromStr = ", ".join(modelChromList)
    modelWindowOpStr = ", ".join(modelWindowOpList)
    modelMergeOpStr = ", ".join(modelMergeOpList)
    cols = [pScore, 
            metrics.r2_score(y_true, y_pred),
            metrics.mean_squared_error( y_true, y_pred),
            metrics.mean_absolute_error( y_true, y_pred),
            metrics.mean_squared_log_error(y_true, y_pred),
            0, 
            aucScoreOPP, 
            corrScoreOP_S, 
            corrScoreOA_S,
            0, 
            corrScoreOP_P, 
            corrScoreOA_P,
            0, 
            modelWindowOpStr,
            modelMergeOpStr,
            pModelParams['normalize'], 
            pModelParams['ignoreCentromeres'],
            pModelParams['conversion'], 
            'MSE', 
            pModelParams['resolution'],
            pSetParams['chrom'], 
            pSetParams['cellType'],
            modelChromStr, 
            modelCellTypeStr]
    cols.extend(valuesOPP)
    df.loc[pTag] = cols
    df = df.sort_values(by=['predictionCellType','predictionChromosome',
                            'modelCellType','modelChromosome', 'conversion',\
                            'Window','Merge', 'normalize'])
    return df
if __name__ == '__main__':
    executePredictionWrapper()
