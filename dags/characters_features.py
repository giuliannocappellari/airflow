from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.providers.mongo.hooks.mongo import MongoHook
from airflow.providers.standard.operators.python import (
    BranchPythonOperator,
    PythonOperator,
)
from airflow.providers.standard.sensors.filesystem import FileSensor
from airflow.utils.trigger_rule import TriggerRule
from transformers.pipelines import pipeline

default_args = {
    "owner": "you",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def extract_characters(file_path: str, **kwargs):
    """
    Read the dropped .txt file, extract each unique character and its raw features.
    Pushes a dict {char: features} to XCom.
    """
    chars = {}
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    for c in set(text):
        chars[c] = {
            "unicode": ord(c),
            "is_alpha": c.isalpha(),
            "count": text.count(c),
        }
    kwargs["ti"].xcom_push(key="chars", value=chars)


def decide_existence(**context):
    """
    Checks MongoDB for each character.
    Returns one of two downstream task IDs depending on whether **all** chars are new.
    """
    chars = context["ti"].xcom_pull(key="chars", task_ids="extract_characters")
    hook = MongoHook(conn_id="mongo_default")
    client = hook.get_conn()
    db = client["characters_db"]
    existing = []
    for c in chars:
        if db.characters.find_one({"char": c}):
            existing.append(c)
    # if any exist, go to conflict resolution; otherwise, go to bulk create
    return "resolve_conflicts" if existing else "create_characters"


def create_characters(**context):
    """Insert all new characters into MongoDB."""
    chars = context["ti"].xcom_pull(key="chars", task_ids="extract_characters")
    hook = MongoHook(conn_id="mongo_default")
    client = hook.get_conn()
    db = client["characters_db"]
    for c, feats in chars.items():
        db.characters.insert_one({"char": c, **feats})


def resolve_conflicts(**context):
    """
    For any character that already exists, compare old vs new features and
    use a small LLM (≤2 B parameters) to reconcile differences.
    """
    chars = context["ti"].xcom_pull(key="chars", task_ids="extract_characters")
    hook = MongoHook(conn_id="mongo_default")
    client = hook.get_conn()
    db = client["characters_db"]

    # LLM setup: OPT-1.3B is ~1.3 B parameters
    reconciler = pipeline("text2text-generation", model="facebook/opt-1.3b", device=0)

    for c, new_feats in chars.items():
        record = db.characters.find_one({"char": c})
        if record:
            prompt = (
                f"Old: {record}\n"
                f"New: {new_feats}\n"
                "Merge these without losing any true facts and resolve any conflicts."
            )
            result = reconciler(prompt, max_length=200)[0]["generated_text"]
            # assume the LLM returns a JSON-like dict
            merged = eval(result)  # convert text → dict (validate in prod!)
            db.characters.replace_one({"char": c}, {"char": c, **merged})


with DAG(
    dag_id="character_ingestion_pipeline",
    default_args=default_args,
    start_date=datetime(2025, 6, 7),
    schedule=None,  # trigger on file drop
    catchup=False,
) as dag:
    wait_for_file = FileSensor(
        task_id="wait_for_file",
        fs_conn_id="fs_default",
        filepath="./include/data/input.txt",
        poke_interval=30,
        timeout=3600,
    )

    extract_characters_op = PythonOperator(
        task_id="extract_characters",
        python_callable=extract_characters,
        op_kwargs={
            "file_path": "./include/data/input.txt",
        },
    )

    decide = BranchPythonOperator(
        task_id="decide_branch",
        python_callable=decide_existence,
    )

    create_characters_op = PythonOperator(
        task_id="create_characters",
        python_callable=create_characters,
    )

    resolve_conflicts_op = PythonOperator(
        task_id="resolve_conflicts",
        python_callable=resolve_conflicts,
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    chain(
        wait_for_file,
        extract_characters_op,
        decide,
        [create_characters_op, resolve_conflicts_op],
    )
