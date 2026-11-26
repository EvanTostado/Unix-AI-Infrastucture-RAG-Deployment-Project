# AI inference tier setup

This tier is separate from the connected frontend/FastAPI mock stack. To test the web form, follow the [repository startup instructions](../README.md#run-the-frontend-and-api); Ollama is not needed. Starting this AI tier does not connect live generation or retrieval to `/api/ask`.

# **1. Download the Files from GITHUB**
This will only download the ai-tier folder
```
# 1. Initialize Git inside your current empty folder
git init

# 2. Point it to your GitHub repository
git remote add origin https://github.com/EvanTostado/Unix-AI-Infrastucture-RAG-Deployment-Project.git

# 3. Turn on sparse-checkout and tell it WHICH subfolder you want
git sparse-checkout init --cone
git sparse-checkout set ai-tier

# 4. Pull from GitHub (only 'ai-tier' will download into this folder!)
git pull origin main
```
You could also just download the whole project with
```
git clone https://github.com/EvanTostado/Unix-AI-Infrastucture-RAG-Deployment-Project.git
```

# **2. Spin up the Docker Container with Compose**
Run from the `ai-tier/` folder. The explicit project name is required by the replica hostnames in `nginx-ai.conf`:
```
docker compose -p ai-tier -f docker-compose.ai.yml up -d
```
-f will designate the docker compose file we are using
-d runs the containers in the background
This may take some time for the first time because it must download ~2gb worth of AI models

# **3. Interacting With the Containers**
First test that its running
```
sudo docker ps
```
There should be 3 containers running the load balancer and 2 ollama containers
Next test the standalone client (Python 3, from `ai-tier/`):
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install requests
python ai_client.py
```

The client uses `http://127.0.0.1:11434` on the host and still contains a Linux administration example. It does not call the web API. For container-to-container access, `http://ai-lb:11434` requires membership in the AI tier's network; the separate web Compose stack does not currently join it.
The script has two main functions:
get_embedding(text)
 - will return the vector for a chunk to be used in the database
 - queries the embedding model
ask_rag_llm(question, retrieved_chunks)
 - will take a question and the relevant chunks taken from the database
 - returns metrics and answer to the question

## Stop the AI tier

From `ai-tier/`:

```bash
docker compose -p ai-tier -f docker-compose.ai.yml down
```

This stops the AI services independently of `docker-compose.web.yml`. The [web integration record](../docs/integration-checks.md) covers the mock path only; AI-tier runtime checks were not part of that verification.
