import json
import os
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


# def extract_characters(input_path: str, output_path: str, **kwargs: dict) -> None:
#     """
#     Read the dropped .txt file, extract each unique character and its raw features.
#     Pushes a list of dicts to XCom under the key "Entity".
#     """
#     global ner, summarizer
#     logger.debug(f"Listing files in {os.path.dirname(input_path)}: {os.listdir(os.path.dirname(input_path))}")
#     with open(input_path, "r", encoding="utf-8") as f:
#         text = f.read()

#     # Lazy-load the NER and summarizer only once per worker
#     if ner is None or summarizer is None:
#         ner = pipeline(
#             "ner",
#             grouped_entities=True,
#             model="dslim/bert-base-NER",
#         )
#         summarizer = pipeline(
#             "summarization",
#             model="sshleifer/distilbart-cnn-12-6",
#         )

#     raw_entities = ner(text)
#     if not raw_entities:
#         logger.warning("No entities found in the input text.")
#         return
#     entities = {e["word"] for e in raw_entities if isinstance(e, dict) and "word" in e}
#     logger.debug(entities)
#     return
#     results = []
#     for ent in entities:
#         sentences = [s for s in text.split(".") if ent in s]
#         context = ". ".join(sentences) or text
#         summary_result = summarizer(
#             context,
#             max_length=50,
#             min_length=5,
#             do_sample=False,
#         )
#         summary_list = list(summary_result) if summary_result is not None else []
#         summary = (
#             summary_list[0].get("summary_text", "")
#             if summary_list and isinstance(summary_list[0], dict)
#             else ""
#         )
#         results.append({"Entity": ent, "summary": summary})

#     os.makedirs(os.path.dirname(output_path), exist_ok=True)
#     with open(output_path, "w") as out:
#         json.dump(results, out)

#     ti = kwargs.get("ti")
#     if ti is not None:
#         ti.xcom_push(key="Entity", value=results)  # type: ignore


def extract_characters(input_path: str, output_path: str, **kwargs: dict) -> None:
    """
    Read the dropped .txt file, extract each character and its raw features.
    Pushes a list of dicts to XCom under the key "Entity".
    """
    openai = OpenAI(
        api_key="LpigfeOnpSmg61F3FToNE8dWq7L5DDVA",
        base_url="https://api.deepinfra.com/v1/openai",
    )
    logger.debug(
        f"Listing files in {os.path.dirname(input_path)}: {os.listdir(os.path.dirname(input_path))}"
    )
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    prompt = (
        "You are a story character extraction assistant. "
        "Extract all unique characters from the text and summarize their features. "
        "Return a JSON array of objects with 'Entity' and 'summary' fields."
        "The JSON schema you must return is ```[{{Entity:<char_name>,summary:<char_summary>}}]```"
    )

    json_format = {
        "type":"json_schema",
        "stric":True,
        "name":"entity",
        "schema":{
            
        }
    }

    chat_completion = openai.chat.completions.create(
        model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
    )

    logger.debug(f"Chat completion response: {chat_completion}")
    logger.debug(
        f"Chat completion response: {json.loads(chat_completion.choices[0].message.content)}"
    )
    return


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


def create_characters(**context: dict) -> None:
    """Insert all new characters into MongoDB."""
    chars = context["ti"].xcom_pull(key="Entity", task_ids="extract_characters")  # type: ignore
    logger.info(f"Chars {chars} will be inserted into MongoDB.")
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
            "input_path": "./include/data/entities_input.txt",
            "output_path": "./include/data/entities_output.json",
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

if __name__ == "__main__":
    extract_characters(
        input_path="./include/data/entities_input.txt",
        output_path="./include/data/entities_output.json",
    )
