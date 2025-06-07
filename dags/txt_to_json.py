import json
import os
from datetime import datetime, timedelta
from logging import getLogger

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.utils import dates, timezone

logger = getLogger()


def process_file(input_path, output_path, **context):
    # read text
    with open(input_path, "r") as f:
        text = f.read()
    # count words
    count = len(text.split())
    # ensure output folder exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # write JSON
    with open(output_path, "w") as f:
        json.dump({"size": count}, f)
    logger.info(f"Processed file {input_path} with word count {count}")


def days_ago(n, hour=0, minute=0, second=0, microsecond=0):
    """
    Get a datetime object representing `n` days ago. By default the time is
    set to midnight.
    """
    today = timezone.utcnow().replace(
        hour=hour, minute=minute, second=second, microsecond=microsecond
    )
    return today - timedelta(days=n)


with DAG(
    dag_id="txt_to_json",
    start_date=days_ago(1),  # or any date ≤ today
    schedule=None,
    catchup=False,
    is_paused_upon_creation=False,  # auto-unpause on load
) as dag:
    count_and_write = PythonOperator(
        task_id="count_and_write",
        python_callable=process_file,
        op_kwargs={
            "input_path": "./include/data/input.txt",
            "output_path": "./include/data/output.json",
        },
    )
