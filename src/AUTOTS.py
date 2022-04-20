import argparse
from os import path, makedirs
from typing import Dict
from autots import AutoTS
import pandas as pd
import sys
from utilities import compute_metrics, load_data, list_files, nmae
import yaml
import mlflow
from tqdm import tqdm
from time import time
from pickle import dump, load

def train_iteration(X: pd.DataFrame, y: pd.Series, config: Dict ={}, run_name: str="", params: Dict = {}):
    """_summary_

    Parameters
    ----------
    X : pd.DataFrame
        _description_
    y : pd.Series
        _description_
    config : Dict, optional
        _description_, by default {}
    run_name : str, optional
        _description_, by default ""
    """
    date_col = config["TS"][params["time_series"]]["date"]
    data = pd.concat([X[date_col], y],axis=1)
    data[date_col] = pd.to_datetime(data[date_col])
    data = data.set_index(date_col)

    K = config["TS"][params["time_series"]]["K"]
    # mlflow configs
    mlflow.set_tracking_uri(config["MLFLOW_URI"])
    try:
        mlflow.create_experiment(name=config["EXPERIMENT"])
    except:
        pass
    
    FDIR = path.join(config["DATA_PATH"], config["MODELS_PATH"], params["time_series"], str(params["iter"]), "AUTOTS")
    makedirs(FDIR, exist_ok=True)
    FPATH = path.join(FDIR, "MODEL.pkl")
    model_params = config["MODELS"]["autots"]

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(params)
        mlflow.log_params(model_params)
        start = time()
        model = AutoTS(
            forecast_length=K,
            **model_params
        )

        model = model.fit(data)
        end = time()
        tr_time = end - start

        with open(FPATH, "wb") as f:
            dump(model, f)

        mlflow.log_metric("training_time", tr_time)
        mlflow.pmdarima.log_model(model, "model")
        
    mlflow.end_run()

def test_iteration(y: pd.Series, config: Dict = {}, run_name: str = "", params: Dict = {}):
    # mlflow configs
    mlflow.set_tracking_uri(config["MLFLOW_URI"])

    experiment = dict(mlflow.get_experiment_by_name(config["EXPERIMENT"]))
    runs = mlflow.search_runs([experiment["experiment_id"]])
    run_id = runs[runs['tags.mlflow.runName']==run_name]["run_id"].values[0]

    # Load model
    FDIR = path.join(config["DATA_PATH"], config["MODELS_PATH"], params["time_series"], str(params["iter"]), "AUTOTS")
    makedirs(FDIR, exist_ok=True)
    FPATH = path.join(FDIR, "MODEL.pkl")
   
    with open(FPATH, "rb") as f:
        model = load(f)

    # Predic and compute metrics
    start = time()
    pred = model.predict().forecast.values.T[0]
    end = time()
    inf_time = (end - start) / len(pred)
    metrics = compute_metrics(y, pred, "ALL", "test_")
    min_v = config["TS"][params["time_series"]]["min"]
    max_v = config["TS"][params["time_series"]]["max"]
    nmae_ = nmae(y, pred, min_v, max_v)
    # Store predictions and target values
    info = pd.DataFrame([y, pred]).T
    info.columns = ["y_true", "y_pred"]
    FDIR = path.join(config["DATA_PATH"], config["PRED_PATH"], params['time_series'], "AUTOTS")
    makedirs(FDIR, exist_ok=True)
    FPATH = path.join(FDIR, f"pred_{str(params['iter'])}.csv")
    info.to_csv(FPATH, index=False)
    # Load new info to mlflow run
    with mlflow.start_run(run_id=run_id) as run:
        mlflow.log_artifact(FPATH)
        mlflow.log_metrics(metrics)
        mlflow.log_metric("test_time", inf_time)
        mlflow.log_metric("test_nmae", nmae_)
    mlflow.end_run()


def main(time_series: str, config: dict = {}, train: bool = True, test: bool = True):
    """_summary_

    Parameters
    ----------
    time_series : str
        _description_
    config : dict, optional
        _description_, by default {}
    """
    # Get train files
    train_files = list_files(time_series, config, pattern="*_tr.csv")
    test_files = list_files(time_series, config, pattern="*_ts.csv")
    if len(train_files) == 0:
        print("Error: no files found!")
        sys.exit()
    # Train AUTOTS models
    target = config["TS"][time_series]["target"]
    for n, file in enumerate(tqdm(train_files)):
        params = {
            'time_series': time_series,
            'target': target,
            'model': "AUTOTS",
            'iter': n+1
        }
        run_name = f"{time_series}_{target}_AUTOTS_{n+1}"
        X, y = load_data(file,config["TS"][time_series]["target"])
        #ta martelado, voltar a ver
        if train:
            train_iteration(X, y, config, run_name, params)
        if test:
           _, y_ts = load_data(test_files[n], target)
           test_iteration(y_ts, config, run_name, params)
        

if __name__ == "__main__":
    # Read arguments
    args = sys.argv[1:]
    parser = argparse.ArgumentParser(description='Generate CV data files for a time-series dataset, based on config.yaml params')
    parser.add_argument(help='Time-series name.', dest="time_series")
    parser.set_defaults(time_series="porto")
    parser.add_argument('-c', '--config', dest='config', 
                        help='Config yaml file.')
    parser.set_defaults(config="config.yaml")
    parser.add_argument('-tr', '--train', dest="train",
                        action=argparse.BooleanOptionalAction,
                        help="Performs model training.")
    parser.set_defaults(train=True)
    parser.add_argument('-ts', '--test', dest="test",
                        action=argparse.BooleanOptionalAction,
                        help="Performs model testing (evaluation).")
    args = parser.parse_args()
    # Load configs
    try:
        config =  yaml.safe_load(open(args.config))
    except Exception as e:
        print("Error loading config file: ", e)
        sys.exit()
    # Train/Test AUTOTS
    main(args.time_series, config, args.train, args.test)