import requests
from typing import List, Dict, Any

# Inside Docker Compose, use "http://ai-lb:11434". From host VM, use "http://127.0.0.1:11434"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
CHAT_MODEL = "qwen2.5:1.5b"
EMBED_MODEL = "nomic-embed-text"


def get_embedding(text: str) -> List[float]:
    """
    Calls Ollama to convert a text chunk or user question into a vector embedding.
    """
    url = f"{OLLAMA_BASE_URL}/api/embed"
    payload = {
        "model": EMBED_MODEL,
        "input": text
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    # Returns the first vector (768 floats for nomic-embed-text), this coresponds to the text you entered into the function
    return data["embeddings"][0]

#you pass the question and the retrived chunks from the database for the ai to use
def ask_rag_llm(question: str, retrieved_chunks: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Sends the user question + retrieved database chunks to Ollama.
    Automatically calculates Time to First Token (TTFT) and Tokens/Sec (TPS),
    and degrades gracefully if all AI replicas are offline.
    """
    # 1. Format the retrieved documents and extract source names
    context_blocks = []
    sources = []
    for chunk in retrieved_chunks:
        source_name = chunk.get("source", "unknown_doc.md")
        sources.append(source_name)
        context_blocks.append(f"\n{chunk.get('content', '')}")

    context_text = "\n\n".join(context_blocks)

    #this can easily be change to what we decide to do
    prompt = (
        "You are a helpful Linux system administration assistant. "
        "Answer the user's question using ONLY the provided context below. "
        "Always mention which [Source] you used.\n\n"
        f"Context:\n{context_text}\n\n"#recieves all of the rag chunks labeled
        f"Question: {question}\n"
        "Answer:"
    )

    #the payload you pass to the load balancer to get passed to the model
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": CHAT_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 2048
        }
    }

    try:
        #if response fails gets caught by the graceful fallback
        response = requests.post(url, json=payload, timeout=120)

        # Check if Nginx rate-limited the request
        if response.status_code == 429:
            return {
                "answer": "Rate limit exceeded (HTTP 429). Please wait a few seconds and try again.",
                "sources": [],
                "metrics": {"status": "rate_limited"}
            }

        response.raise_for_status()
        data = response.json()

        #calculate all the metrics from the response
        # 2. Extract AI Observability Metrics (Ollama returns durations in nanoseconds)
        prompt_eval_ns = data.get("prompt_eval_duration", 0)
        load_ns = data.get("load_duration", 0)
        eval_ns = data.get("eval_duration", 1)  # avoid division by zero
        eval_count = data.get("eval_count", 0)
        total_ns = data.get("total_duration", 0)

        # Time to First Token (TTFT) = model load time + prompt evaluation time
        ttft_seconds = (load_ns + prompt_eval_ns) / 1e9
        # Tokens Per Second (TPS) = generated tokens / generation time in seconds
        tokens_per_sec = eval_count / (eval_ns / 1e9) if eval_ns > 0 else 0.0
        total_latency_sec = total_ns / 1e9

        return {
            "answer": data.get("response", "").strip(),
            "sources": list(set(sources)),
            "metrics": {
                "status": "ok",
                "ttft_seconds": round(ttft_seconds, 3),
                "tokens_per_second": round(tokens_per_sec, 2),
                "total_latency_seconds": round(total_latency_sec, 3),
                "tokens_generated": eval_count
            }
        }

    except requests.exceptions.RequestException as e:
        # 3. Graceful Degradation (Required by Rubric when all AI replicas are down)
        return {
            "answer": (
                "The AI assistant is temporarily unavailable while inference nodes recover. "
                "You can still browse the Linux documentation directly."
            ),
            "sources": [],
            "metrics": {
                "status": "degraded_fallback",
                "error": str(e)
            }
        }


# Quick test when running `python3 ai_client.py` directly
if __name__ == "__main__":
    #simulates the output from the database
    sample_docs = [
        {
            "source": "lvm_guide.md",
            "content": "To create an LVM logical volume named lv_db of size 10G in volume group vg_data, run: sudo lvcreate -L 10G -n lv_db vg_data"
        }
    ]
    result = ask_rag_llm("How do I create a 10GB logical volume named lv_db?", sample_docs)
    print("AI Answer:\n", result["answer"])
    print("\nSources Used:", result["sources"])
    print("Observability Metrics:", result["metrics"])
