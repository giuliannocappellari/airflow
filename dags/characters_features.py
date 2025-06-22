import json
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.models.param import ParamsDict
from airflow.providers.mongo.hooks.mongo import MongoHook
from airflow.providers.standard.operators.python import (
    BranchPythonOperator,
    PythonOperator,
)
from airflow.providers.standard.sensors.filesystem import FileSensor
from airflow.sdk import Param
from airflow.utils.trigger_rule import TriggerRule
from loguru import logger
from openai import OpenAI
from transformers.pipelines import pipeline

default_args = {
    "owner": "you",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

ner = None
summarizer = None


def extract_characters(
    input_path: str, output_path: str, model: str, **kwargs: dict
) -> None:
    """
    Extract unique characters from the input text and summarise each one with
    an OpenAI-compatible model.  The model is asked to produce a JSON list of
    objects that match the schema:
        [
          {
            "Entity":  "<character name>",
            "summary": "<one-sentence summary>"
          }
        ]
    The resulting list is:
        written to ``output_path`` for offline inspection
        pushed to XCom under the key ``Entity`` so downstream tasks can use it
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in the container environment.")
    openai = OpenAI(api_key=api_key, base_url="https://api.deepinfra.com/v1/openai")

    logger.debug(f"Model: {model}")
    logger.debug(f"kwargs: {kwargs}")
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    system_prompt = (
        "You are a story-analysis assistant. "
        "Return **only** a JSON array where each element has:\n"
        "  • Entity  – the exact character name (string)\n"
        "  • summary – a concise description (string, ≤ 25 words)\n"
        "No additional keys, no prose outside the JSON."
        "Example:\n"
        '  [{"Entity": "Alice", "summary": "A curious '
        '   woman who explores a fantastical world."}, \n'
        '   {"Entity": "Bob", "summary": "A brave knight '
        '   who fights dragons."}]'
    )

    chat_completion = openai.chat.completions.create(
        model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0,
    )
    response_content = chat_completion.choices[0].message.content
    if not response_content:
        logger.error("Model did not return any content. Aborting run.")
        return
    try:
        entities = json.loads(response_content)
        assert isinstance(entities, list)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as out:
            json.dump(entities, out)

        ti = kwargs.get("ti")
        if ti is not None:
            ti.xcom_push(key="Entity", value=entities)  # type: ignore
    except Exception as e:
        logger.error("Model did not return valid JSON. Aborting run.")


def decide_existence(**context: dict) -> str:
    """
    Check MongoDB for each character.
    Returns 'resolve_conflicts' if any already exist, else 'create_characters'.
    """
    chars = context["ti"].xcom_pull(key="Entity", task_ids="extract_characters")  # type: ignore
    hook = MongoHook(mongo_conn_id="mongo_default")
    client = hook.get_conn()

    db = client["characters_db"]

    existing = [
        ent for ent in chars if db.characters.find_one({"Entity": ent["Entity"]})
    ]

    return "resolve_conflicts" if existing else "create_characters"


def create_characters(model: str, **context: dict) -> None:
    """
    Insert all new characters into MongoDB, tagging each one with
    the model that generated it.

    Parameters
    ----------
    model : str
        Name (or version) of the model used to create the characters.
    **context : dict
        Airflow context dict (expects XCom key `"Entity"`).
    """

    chars = context["ti"].xcom_pull(key="Entity", task_ids="extract_characters")  # type: ignore

    if not chars:
        logger.warning("No characters returned from XCom; nothing to insert.")
        return

    for char in chars:
        if isinstance(char, dict):
            char.setdefault("metadata", {})["model"] = model

    logger.info("Inserting %d characters into MongoDB: %s", len(chars), chars)

    hook = MongoHook(mongo_conn_id="mongo_default")
    client = hook.get_conn()
    db = client["characters_db"]
    db.characters.insert_many(chars)


def resolve_conflicts(**context: dict) -> None:
    """
    For any character that already exists, reconcile old vs. new summaries
    using a small text-generation model.
    """
    chars = context["ti"].xcom_pull(key="Entity", task_ids="extract_characters")  # type: ignore
    hook = MongoHook(mongo_conn_id="mongo_default")
    client = hook.get_conn()
    db = client["characters_db"]

    # Use a smaller model during local dev to avoid OOMs
    reconciler = pipeline(
        "text2text-generation",
        model="facebook/opt-350m",
        device=0,
    )

    logger.info("Model loaded")
    for ent in chars:
        record = db.characters.find_one({"Entity": ent["Entity"]})
        if record:
            prompt = (
                f"Old: {record}\n"
                f"New: {ent['summary']}\n"
                "Merge these without losing any true facts and resolve conflicts."
            )
            result = reconciler(prompt, max_length=200)
            if result and isinstance(result, list):
                result = result[0]["generated_text"]

                try:
                    merged = json.loads(result)
                except json.JSONDecodeError:
                    logger.warning("Model output is not valid JSON, skipping.")
                    continue

                db.characters.replace_one(
                    {"Entity": ent["Entity"]},
                    {"Entity": ent["Entity"], **merged},
                )


with DAG(
    dag_id="character_ingestion_pipeline",
    default_args=default_args,
    start_date=datetime(2025, 6, 7),
    schedule=None,  # manual trigger or file drop
    params=ParamsDict(
        {
            "llm": Param(
                "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                type="string",
                enum=[
                    "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                    "deepseek-ai/DeepSeek-R1-0528",
                    "deepseek-ai/DeepSeek-V3-0324",
                    "google/gemma-3-27b-it",
                ],
            )
        }
    ),
    render_template_as_native_obj=True,
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
            "input_path": "./include/data/entities_input.txt",
            "output_path": "./include/data/entities_output.json",
            "model": "{{ params.llm }}",
        },
    )

    decide = BranchPythonOperator(
        task_id="decide_branch",
        python_callable=decide_existence,
    )

    create_characters_op = PythonOperator(
        task_id="create_characters",
        python_callable=create_characters,
        op_kwargs={
            "model": "{{ params.llm }}",
        },
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

if __name__ == "__main__":
    extract_characters(
        input_path="./include/data/entities_input.txt",
        output_path="./include/data/entities_output.json",
        model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    )
