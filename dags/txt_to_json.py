import json
import os
from logging import getLogger

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from utils import days_ago

logger = getLogger()


def process_file(input_path, output_path, **context):

    with open(input_path, "r") as f:
        text = f.read()
    count = len(text.split())
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({"size": count}, f)
    logger.info(f"Processed file {input_path} with word count {count}")


with DAG(
    dag_id="txt_to_json",
    start_date=days_ago(1),
    schedule=None,
    catchup=False,
    is_paused_upon_creation=False,
) as dag:
    count_and_write = PythonOperator(
        task_id="count_and_write",
        python_callable=process_file,
        op_kwargs={
            "input_path": "./include/data/input.txt",
            "output_path": "./include/data/output.json",
        },
    )
