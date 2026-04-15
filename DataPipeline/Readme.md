# LangScope Enterprise Data Maintenance Engine

*An enterprise-grade, fault-tolerant data ingestion and search pipeline designed to synchronize massive Hugging Face datasets into an optimized Elasticsearch vector/BM25 hybrid backend, accessible via the Model Context Protocol (MCP) and its direct HTTP API.*

---

## Architecture Overview

The LangScope Data Engine is distributed across highly specialized, serverless microservices to handle massive data loads without bottlenecks:

1. **The Sync Engine (Distributed ETL Squads):** A fan-out distributed job that performs differential state-tracking via MongoDB. It intelligently queries Hugging Face commit timestamps and selectively downloads only changed datasets, completely bypassing up-to-date domains to save bandwidth and compute time.
2. **The Dynamic Indexer (Queue Workers):** A message queue-driven microservice built to handle unpredictable data shapes. It dynamically scales its memory footprint and thread count in real-time based on the exact byte-density of the incoming data stream.
3. **The Search Backend:** A robust Elasticsearch cluster holding millions of unified documents across a single, master index (`langscope-unified-data`). This schema-agnostic approach prevents mapping explosions and eliminates cluster thrashing during cross-domain fallback searches.
4. **The Dual-Protocol Server:** A unified FastAPI server that exposes both an Anthropic FastMCP endpoint for autonomous AI agents and an MCP HTTP API (`/api/search`) for direct microservice communication.

---

## ?? Intelligent Search Features (RAG-Optimized)

The Search Server (`mcp_search_server.py`) is an intelligent middleware layer specifically optimized for Large Language Models:

* **LLM Intent Translation:** Automatically intercepts raw user queries, strips out domain-specific noise (e.g., "Automatic Speech Recognition"), appends international locale codes (e.g., "es-ES"), and extracts strict semantic keywords before querying Elasticsearch.
* **Cascading Hybrid Scoring:** Executes a simultaneous 3-pronged search using Exact Phrase Matching (Boost: 50), Fuzzy NLP Semantic Matching (Boost: 20), and Fallback Filename Wildcards (Boost: 5) to guarantee highly relevant data retrieval across diverse datasets.
* **On-the-Fly Media Resolution:** Seamlessly catches internal `hf://` Hugging Face protocols and translates them into valid, downloadable HTTPS URLs. It also securely generates time-buffered Azure Blob SAS tokens dynamically at query time.
* **Recursive Deep Clean Filtering:** Automatically traverses deep into nested JSON structures to purge useless Pandas artifacts (`"Unnamed: 0"`), arbitrary `pubids`, conversational termination artifacts, and completely empty nested dictionaries or lists. This aggressively preserves LLM context window tokens.
* **Context-Aware Schema Injection:** Intelligently analyzes the payload shape before returning it. If the returned data consists solely of media URLs with no text context (e.g., audio file links), the server automatically injects a `DOMAIN_HEADER` schema definition at the top of the array to prevent LLM hallucinations.
* **Zero-Overhead API Responses:** The endpoint bypasses standard Pydantic serialization overhead, piping the pre-computed JSON strings directly over the network to guarantee maximum throughput when delivering massive contextual payloads to LLM agents.

---

## Prerequisites & Dependencies

### 1. For Calling the API (Client / Evaluation Scripts)
If you are only querying the engine from a remote script or AWS container, you **do not** need the backend libraries. You only need the standard HTTP library:

```bash
pip install "requests>=2.31.0"
```

### 2. For Hosting the Data Engine (Server-Side)
To run the ETL pipelines, the Dynamic Indexer, or host the FastAPI server yourself, ensure your environment has the full backend stack installed. 
*(Note: `pymongo` is strictly pinned below version 4.4 to maintain compatibility with Azure Cosmos DB's MongoDB 4.0 API).*

Run the following command to install all dependencies in one go:

```bash
pip install "pymongo<4.4" "requests>=2.31.0" "elasticsearch>=8.12.0" "azure-storage-blob>=12.19.0" "azure-storage-queue>=12.9.0" "mcp>=1.2.0" "fastapi>=0.110.0" "openai>=1.14.0" "pandas>=2.2.0" "pyarrow>=15.0.0" uvicorn
```
*(Note: `uvicorn` is included to act as the ASGI server for the FastAPI deployment).*

---

## Key Engineering Features

### 1. Zero-OOM Dynamic Pre-Profiling
Processing NLP datasets means dealing with unpredictable row sizes (e.g., standard CSVs vs. JSON files with 10 Million context tokens per row).
* **The Pre-Profiler:** Before loading data into memory, the indexer intercepts the network stream, physically peeks at the first 2MB of data, counts the delimiters (like `\n` or `}`), and mathematically deduces the row density.
* **The Shield:** It uses this density calculation to throttle the initial Pandas `chunksize` down to single digits for massive files, completely eliminating the risk of JVM Garbage Collection Death Spirals or Container OOM Kills.

### 2. CPU-Aware Adaptive Throttling
Passing millions of tokens through Elasticsearch's text analyzers requires massive CPU overhead. The indexer automatically detects when it is processing heavy context files and dynamically drops to single-threaded ingestion (`thread_count=1`), increases timeout thresholds, and adapts its logging intervals to protect the database from being overwhelmed.

### 3. Differential Syncing & Audit Logging
The ETL pipeline tracks the exact state of every domain in MongoDB (`etl_run_history`). If a dataset hasn't been updated on Hugging Face since the last run, the ETL workers instantly skip it. This reduces sync times for unchanged 80GB datasets from hours to mere seconds.

### 4. Self-Healing State Recovery
If a worker node is forcefully evicted or crashes mid-download, the pipeline detects the `FAILED` or `INCOMPLETE` state on the next run. It automatically purges the corrupted landing zone in Object Storage and re-downloads a pristine copy to ensure 100% data integrity.

### 5. Deterministic Upsert Anchoring
To prevent data duplication during delta syncs, the Indexer generates immutable, deterministic MD5 hashes for every document based strictly on the source filename and raw row content. This transforms the ingestion pipeline from a risky "Append" model into a highly stable "Upsert" (Update/Insert) model, ensuring zero duplication even if a dataset is processed 100 times.

---

## MCP HTTP API Integration Guide

The LangScope Data Engine exposes an MCP Direct API (`/api/search`), allowing any Python codebase (such as evaluation pipelines or battle scripts) to fetch real-world context directly into memory without requiring custom SDKs or client scripts.

This endpoint executes the exact same intent-parsing and search logic as the agentic MCP protocol, but delivers it over a standard HTTP POST request.

### Step 1: Environment Configuration
The MCP HTTP endpoint requires two environment variables to securely connect. Export these in your terminal, use a `.env` file, or configure them in your deployment environment:

* `MCP_API_URL`: `https://langscope-mcp-server.mangoplant-e08825a0.eastus.azurecontainerapps.io/api/search`
* `MCP_API_KEY`: `<YOUR_SECRET_API_KEY>`

### Step 2: Generic Python Integration
You can use Python's standard `requests` library to query the engine. The response is a dynamic, schema-agnostic JSON payload containing the raw data rows.

```python
import os
import requests
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def fetch_context(domain_to_search: str, query: str, limit: int = 5):
    url = os.getenv("MCP_API_URL")
    api_key = os.getenv("MCP_API_KEY")
    
    headers = {"x-api-key": api_key}
    payload = {
        "domain": domain_to_search,
        "query": query,
        "limit": limit
    }
    
    logger.info(f"Searching domain: '{domain_to_search}'...")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        
        results = response.json()
        logger.info("Search successful. Injected context retrieved.")
        return results
            
    except requests.exceptions.RequestException as e:
        logger.error(f"API Execution Error: {e}")
        return None

if __name__ == "__main__":
    search_domain = "Medical"
    natural_language_query = "Find documents mentioning recent advancements in CRISPR technology"
    
    context_data = fetch_context(search_domain, natural_language_query, limit=3)
    if context_data:
        logger.info(f"\n{json.dumps(context_data, indent=2)}")
```

### Step 3: Integration Example in an LLM Workflow

If you are generating test cases or running LLM battles, you can use the fetched context to ground the LLM's prompt using the Retrieval-Augmented Generation (RAG) pattern.

```python
import os
import json
import requests
import logging

logger = logging.getLogger(__name__)

def _generate_battle_case(domain: str) -> str:
    url = os.getenv("MCP_API_URL")
    api_key = os.getenv("MCP_API_KEY")
    
    logger.info(f"Fetching context for battle domain: {domain}")
    
    try:
        response = requests.post(
            url, 
            json={"domain": domain, "query": "Find a challenging real-world scenario", "limit": 2}, 
            headers={"x-api-key": api_key},
            timeout=60
        )
        response.raise_for_status()
        context_data = response.json()
    except Exception as e:
        logger.error(f"Failed to fetch context: {e}")
        context_data = {"error": str(e)}
    
    # Inject dynamic context into your LLM prompt
    prompt = f"""Use the following context to generate a realistic evaluation case:
    
<context>
{json.dumps(context_data, indent=2)}
</context>

Create a complex case based on the above information."""

    # Call your LLM client as usual
    # response = my_llm_client.chat(messages=[{"role": "user", "content": prompt}])
    return prompt
```