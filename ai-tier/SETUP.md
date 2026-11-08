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
Just run this command and it will set up everything (you must be in the same folder)
```
sudo docker compose -f docker-compose.ai.yml up -d
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
Next test the connection
```
python ai_client.py
```
The script has two main functions:
get_embedding(text)
 - will return the vector for a chunk to be used in the database
 - queries the embedding model
ask_rag_llm(question, retrieved_chunks)
 - will take a question and the relevant chunks taken from the database
 - returns metrics and answer to the question

# **4. If I missed anything ask me and ill edit this**
