# What is this project?

This repository contains an Apache Airflow pipeline for basic text processing tasks, managed locally with the `uv` environment tool and run via the Astronomer (`astro`) CLI.

# Current functionality

* **`txt_to_json` DAG**: Reads a plain text file, counts its words, and writes a JSON object with the word count (`{"size": <word_count>}`).
* **`entity_extraction_summarization` DAG**: Loads a text file, uses a small local LLM to perform Named Entity Recognition and summarization for each entity, and outputs a JSON mapping each entity to its summary.

# Prerequisites

* **Python 3.9+**
* **Docker** & **Docker Compose** (for Astronomer dev environment)
* **uv** (for local Python environment management)
* **Astronomer CLI (`astro`)**

---

# 1. Install and use `uv` for local Python envs

1. **Install** `uv` (you only need to do this once):

   ```bash
   pip install uv
   ```

2. **Initialize** a new environment (creates `packages.txt`):

   ```bash
   uv init
   ```

3. **Add new libraries** to your environment:

   ```bash
   uv add requests
   uv add "transformers>=4.30.0" torch>=2.0.0
   ```

4. **Generate a lock file** (pins exact versions):

   ```bash
   uv lock
   ```

5. **Sync** your virtual environment with the lock file:

   ```bash
   uv sync
   ```

All dependencies will be tracked in `packages.txt` and the corresponding lock file.

---

# 2. Install Airflow with Astronomer CLI

1. **macOS (Homebrew):**

   ```bash
   brew install astronomer/tap/astro-cli
   ```

2. **Linux:**

   ```bash
   curl -sSL https://install-cli.astronomer.io | sudo bash
   ```

3. **Verify installation:**

   ```bash
   astro version
   ```

---

# 3. Develop and deploy with Astronomer

## Local development

1. **Start** your local Airflow environment (scheduler, webserver, worker):

   ```bash
   astro dev start
   ```

2. **Access** the Airflow UI at [http://localhost:8080](http://localhost:8080)

3. **Stop** the environment when done:

   ```bash
   astro dev stop
   ```

## Deploy to Astronomer Cloud or Enterprise

1. **Log in** to your Astronomer host (e.g. astro.cloud):

   ```bash
   astro login <HOST_URL>
   ```

2. **Set** your deployment context:

   ```bash
   astro config set deployment <DEPLOYMENT_NAME>
   ```

3. **Deploy** your DAGs and images:

   ```bash
   astro deploy <DEPLOYMENT_NAME>
   ```

---

# 4. Testing locally with Astronomer

Use the `astro dev run` shortcut to execute Airflow CLI commands inside your running containers:

* **List DAGs:**

  ```bash
  astro dev run airflow dags list
  ```

* **Parse DAG files:**

  ```bash
  astro dev run airflow dags list  # verifies import without errors
  ```

* **Test a single task:**

  ```bash
  astro dev run airflow tasks test <dag_id> <task_id> <YYYY-MM-DD>
  ```

  *Example:*

  ```bash
  astro dev run airflow tasks test txt_to_json count_and_write 2025-06-07
  ```

* **View logs in real-time:**

  ```bash
  astro dev logs scheduler
  astro dev logs worker
  ```

---

# 5. Project Structure

```
├── Dockerfile
├── airflow_settings.yaml    # custom Airflow config overrides
├── packages.txt             # uv-managed dependency list
├── packages.lock            # uv lockfile (pins versions)
├── include/                 # auto-mounted into containers at /include
│   └── data/
│       ├── input.txt
│       └── ...
├── dags/                    # your DAG definitions
│   ├── txt_to_json.py
│   └── entity_extraction_summarization.py
├── plugins/                 # custom operators/hooks
├── tests/                   # unit/integration tests
└── README.md                # this file