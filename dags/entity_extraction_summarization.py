import json
import os

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from utils import days_ago

ner = None
summarizer = None


def extract_and_summarize(input_path: str, output_path: str, **kwargs):
    global ner, summarizer

    if ner is None or summarizer is None:
        from transformers import pipeline

        ner = pipeline(
            "ner",
            grouped_entities=True,
            model="dslim/bert-base-NER",
        )
        summarizer = pipeline(
            "summarization",
            model="sshleifer/distilbart-cnn-12-6",
        )

    with open(input_path, "r") as f:
        text = f.read()
    raw_entities = ner(text)
    entities = {e["word"] for e in raw_entities}
    results = {}
    for ent in entities:
        sentences = [s for s in text.split(".") if ent in s]
        context = ". ".join(sentences) or text
        summary = summarizer(context, max_length=50, min_length=5, do_sample=False)[0][
            "summary_text"
        ]
        results[ent] = summary

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as out:
        json.dump(results, out)


with DAG(
    dag_id="entity_extraction_summarization",
    start_date=days_ago(1),
    schedule=None,
    catchup=False,
    is_paused_upon_creation=False,
) as dag:
    PythonOperator(
        task_id="extract_and_summarize",
        python_callable=extract_and_summarize,
        op_kwargs={
            "input_path": "./include/data/entities_input.txt",
            "output_path": "./include/data/entities_output.json",
        },
    )
