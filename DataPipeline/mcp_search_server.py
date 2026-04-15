import mimetypes
import os, json, base64, urllib.parse, httpx, logging, sys, re
from datetime import datetime, timedelta, timezone

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Request, Depends, HTTPException, Header, Response
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from openai import AsyncOpenAI
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

from pymongo import MongoClient

# ==========================================
# CONFIGURATION & LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("LangScope_MCP")

ES_USER, ES_PASS = os.getenv("ELASTIC_USER"), os.getenv("ELASTIC_PASS")
ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY") 
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "llama-3.3-70b-versatile")
AZURE_CONN_STR = os.getenv("AZURE_STORAGE_CONN_STR")
MONGO_URI = os.getenv("MONGO_URI")

EXPECTED_API_KEY = os.getenv("MCP_API_KEY", "langscope-dev-key")
SINGLE_INDEX_NAME = "langscope-unified-data"

es = AsyncElasticsearch(ES_URL, basic_auth=(ES_USER, ES_PASS) if ES_USER else None, request_timeout=120)
blob_service = BlobServiceClient.from_connection_string(AZURE_CONN_STR) if AZURE_CONN_STR else None
AZURE_ACCOUNT_KEY = None
if AZURE_CONN_STR:
    parts = dict(item.split('=', 1) for item in AZURE_CONN_STR.split(';') if '=' in item)
    AZURE_ACCOUNT_KEY = parts.get('AccountKey')

llm_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, http_client=httpx.AsyncClient(proxies=None, timeout=30.0)) if LLM_API_KEY else None

mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None
db = mongo_client["langscope"] if mongo_client else None

mcp = FastMCP("LangScope Search", dependencies=["elasticsearch", "openai", "azure-storage-blob", "pymongo"])

# ==========================================
# CORE UTILITIES
# ==========================================
def extract_media_urls(payload, account_url, unique_urls):
    stack = [payload]
    while stack:
        curr = stack.pop()
        if isinstance(curr, dict):
            for k, v in curr.items():
                if isinstance(v, str):
                    v_lower = v.lower()
                    if v_lower.startswith(account_url) or "huggingface.co" in v_lower or v_lower.startswith("hf://"):
                        unique_urls.add(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(curr, list):
            stack.extend(curr)

def fetch_media_for_llm(blob_url):
    try:
        parsed_url = urllib.parse.urlparse(blob_url)
        url_path = urllib.parse.unquote(parsed_url.path.lstrip('/'))
        container_name, blob_name = url_path.split("/", 1)
        
        clean_blob_name = blob_name.lstrip('/') 
        now = datetime.now(timezone.utc)
        
        sas = generate_blob_sas(
            account_name=blob_service.account_name,
            container_name=container_name, 
            blob_name=clean_blob_name, 
            account_key=AZURE_ACCOUNT_KEY, 
            permission=BlobSasPermissions(read=True), 
            start=now - timedelta(minutes=15), 
            expiry=now + timedelta(hours=2)    
        )
        
        safe_blob_path = urllib.parse.quote(clean_blob_name)
        sas_url = f"https://{blob_service.account_name}.blob.core.windows.net/{container_name}/{safe_blob_path}?{sas}"
        
        return blob_url, sas_url
    except Exception as e:
        logger.error(f"Azure SAS generation failed: {blob_url}: {e}")
        return blob_url, blob_url

def replace_urls_in_payload(payload, url_map):
    if isinstance(payload, str): return url_map.get(payload, payload)
    if isinstance(payload, dict): return {k: replace_urls_in_payload(v, url_map) for k, v in payload.items()}
    if isinstance(payload, list): return [replace_urls_in_payload(v, url_map) for v in payload]
    return payload

def enrich_results_with_media(results):
    if not blob_service or not results: return results 
    
    account_url = f"https://{blob_service.account_name.lower()}.blob.core.windows.net/"
    unique_urls = set()
    
    extract_media_urls(results, account_url, unique_urls)
    if not unique_urls: return results

    url_map = {}

    for u in unique_urls:
        u_lower = u.lower() 
        
        if u_lower.startswith(account_url):
            try:
                orig, sas_url = fetch_media_for_llm(u)
                url_map[orig] = sas_url
            except Exception as e:
                logger.error(f"Media enrichment task failed for {u}: {e}")
            
        elif "huggingface.co" in u_lower:
            final_u = u
            if "download=true" not in u_lower:
                separator = "&" if "?" in u else "?"
                final_u = f"{u}{separator}download=true"
            url_map[u] = final_u
            
        elif u_lower.startswith("hf://"):
            try:
                parts = u[5:].split('/', 3) 
                if len(parts) == 4 and parts[0] == "datasets":
                    hf_https_url = f"https://huggingface.co/datasets/{parts[1]}/{parts[2]}/resolve/main/{parts[3]}?download=true"
                    url_map[u] = hf_https_url
                else:
                    url_map[u] = u
            except Exception:
                url_map[u] = u

    return replace_urls_in_payload(results, url_map)

# ==========================================
# MCP TOOLS & CORE SEARCH LOGIC
# ==========================================
@mcp.tool()
async def natural_language_search(domain: str, natural_query: str, limit: int = 5) -> str:
    if not llm_client: return "Error: LLM_API_KEY is missing."
    if not domain: return "Error: 'domain' parameter is mandatory."
    
    # ==========================================
    # DYNAMIC PROMPT ROUTING (Zero Latency)
    # ==========================================
    is_advanced_model = any(x in LLM_MODEL_NAME.lower() for x in ["70b", "8x7b", "gpt-4", "claude"])

    if is_advanced_model:
        logger.info(f"[ROUTER] Advanced Model Detected ({LLM_MODEL_NAME}). Using 70B Master Prompt.")
        prompt = f"""
        You are a strictly automated Search Query Analyzer for an advanced Elasticsearch system.
        Your ONLY job is to parse the user's natural language query into a precise JSON search intent object.
        
        Input Domain: '{domain}'
        Input Query: '{natural_query}'
        
        INSTRUCTIONS:
        1. Extract 'strict_keywords' (String): The core text needed to match database rows.
           - Exclude filler words, conversational text, and generic intent words (e.g., "find", "show", "database").
           - Ignore terms that merely describe the domain itself.
           - Keep spoken languages as standard text keywords without appending locale codes.
           - DO NOT include numbers or math operators here if they are extracted in Step 3.
           
        2. Classify 'task_type' (String): Categorize the query into a broad technical Task Type.
           - Examples: "Text Generation", "Visual Question Answering", "Automatic Speech Recognition", "Code Execution", "Finance".
           - If unsure, output "General Retrieval".
           
        3. Extract 'numeric_filters' (Array of Objects): Apply logical reasoning to extract numerical conditions.
           - ONLY extract math if the user explicitly implies a measurable condition (e.g., "greater than 50", "under 100", "exactly 5", "over $20").
           - INFER THE FIELD: Dynamically guess the logical database column name based on context.
           - OPERATORS: You MUST map the intent to one of these exact string operators: "gt" (>), "gte" (>=), "lt" (<), "lte" (<=), "eq" (==).
           - GUARDRAIL: You must possess the intelligence to ignore raw identifiers, years (1990s), financial quarters (Q3), and media formats (4k, 720p, 16:9, 60fps). If no valid measurable condition exists, return [].
           
        4. Extract 'requires_media' (Boolean): Return true ONLY if the query explicitly asks for media files. Otherwise, return false.
        
        CRITICAL AUTOMATION RULES:
        - You must output ONLY valid, parsable JSON.
        - DO NOT wrap the JSON in markdown blocks (e.g., no ```json).
        - DO NOT output any conversational text, greetings, or explanations.
        
        EXPECTED JSON SCHEMA:
        {{
            "strict_keywords": "string",
            "task_type": "string",
            "numeric_filters": [],
            "requires_media": boolean
        }}
        (Note for numeric_filters: Only inject objects formatted as {{"field": "string", "operator": "string", "value": number}} if a valid, measurable math condition exists).
        """
    else:
        logger.info(f"[ROUTER] Fast/Small Model Detected ({LLM_MODEL_NAME}). Using 8B Hardened Prompt.")
        prompt = f"""
        You are a strictly automated Search Query Analyzer for an advanced Elasticsearch system.
        Your ONLY job is to parse the input query into a specific JSON format.
        
        Input Domain: '{domain}'
        Input Query: '{natural_query}'
        
        INSTRUCTIONS:
        1. Extract 'strict_keywords' (String): The core text needed to match database rows.
           - Exclude filler words, conversational text, and generic intent words (e.g., "Here is a query", "find", "dataset", "please").
           - Ignore terms that merely describe the domain itself.
           - Extract ONLY the core subjects, nouns, and descriptive adjectives.
           - Keep spoken languages as standard text keywords without appending locale codes.
           
        2. Classify 'task_type' (String): Categorize the query into a broad technical Task Type.
           - Examples: "Text Generation", "Visual Question Answering", "Automatic Speech Recognition", "Code Execution", "Clinical Reasoning", "Finance".
           - If unsure, output "General Retrieval".
           
        3. Extract 'requires_media' (Boolean): Return true ONLY if the query explicitly asks for media files (e.g., "audio", "mp3", "video", "image", "photo", "wav", "recording"). Otherwise, return false.
        
        CRITICAL RULES FOR AUTOMATED PIPELINE:
        - You must output ONLY valid, parsable JSON.
        - DO NOT wrap the JSON in markdown blocks (e.g., no ```json).
        - DO NOT output any conversational text, greetings, or explanations before or after the JSON.
        - DO NOT add any extra keys to the JSON object.
        
        EXPECTED JSON SCHEMA:
        {{
            "strict_keywords": "string",
            "task_type": "string",
            "requires_media": boolean
        }}
        """
    
    try:
        # --- 1. LOG THE OUTGOING REQUEST ---
        logger.info(f"[LLM REQUEST] Sending prompt to {LLM_MODEL_NAME}...\n{prompt}")
        
        # Pre-calculate a smart fallback just in case the LLM crashes
        media_keywords = ["audio", "video", "mp3", "mp4", "image", "photo", "wav", "recording", "media"]
        fallback_media_req = any(mk in natural_query.lower() for mk in media_keywords)

        try:
            # Attempt to call the LLM
            res = await llm_client.chat.completions.create(
                model=LLM_MODEL_NAME, 
                messages=[{"role": "user", "content": prompt}], 
                response_format={"type": "json_object"} 
            )
            raw_response = res.choices[0].message.content
            
            # --- 2. LOG THE RAW LLM RESPONSE ---
            logger.info(f"[LLM RESPONSE] Raw output from {LLM_MODEL_NAME}:\n{raw_response}")
            
            # Parse the JSON intent
            intent = json.loads(raw_response)

        except Exception as llm_error:
            # --- 3. THE FALLBACK ENGINE ---
            logger.warning(f"🚨 [LLM FAILURE] {llm_error}. Engaging Heuristic Fallback Search!")
            intent = {
                "strict_keywords": natural_query, # Just use the user's raw text
                "task_type": "",                  # Skip sibling domains
                "numeric_filters": [],            # Skip math filters
                "requires_media": fallback_media_req # Use our Python keyword guesser!
            }

        # Extract the final variables (whether from the LLM or the Fallback)
        strict_keywords = intent.get("strict_keywords", natural_query)
        groq_task_type = intent.get("task_type", "")
        # Safe extraction: 8B prompt won't return this, so it safely defaults to []
        numeric_filters = intent.get("numeric_filters", []) 
        requires_media = intent.get("requires_media", fallback_media_req) 
        
        if isinstance(strict_keywords, list): strict_keywords = " ".join(str(k) for k in strict_keywords)
        
        logger.info(f"[FINAL INTENT] Keywords: '{strict_keywords}' | Task: '{groq_task_type}' | Media Req: {requires_media} | Math: {numeric_filters}")
        
        # 2. Sibling Domain Discovery
        target_domains = [domain]
        if db is not None and groq_task_type:
            siblings = db["ground_truth"].find({"Task_Type": groq_task_type}, {"Domain": 1})
            sibling_domains = [s["Domain"] for s in siblings if s.get("Domain") and s.get("Domain") != domain]
            target_domains.extend(sibling_domains)
            
        clean_keys = re.sub(r'[^\w\s-]', ' ', strict_keywords)
        wildcard_keywords = " OR ".join([f"*{w.lower().strip()}*" for w in clean_keys.split() if w.strip()])
        
        # 3. Build the Base Query Framework
        es_query_body = {
            "query": {
                "bool": {
                    "filter": [
                        {"terms": {"domain": target_domains}}
                    ],
                    "must": [], 
                    "should": [
                        {"term": {"domain": {"value": domain, "boost": 100.0}}}
                    ]
                }
            },
            "size": limit + 2
        }

        # ✨ NEW: Inject the Media Boost
        if requires_media:
            es_query_body["query"]["bool"]["should"].extend([
                {"exists": {"field": "raw_payload.media_url", "boost": 500.0}},
                {"exists": {"field": "raw_payload.file_path", "boost": 300.0}}
            ])

        # 3b. Only inject text search if strict_keywords actually has text
        if strict_keywords.strip():
            es_query_body["query"]["bool"]["must"].append({
                "bool": {
                    "should": [
                        {"match_phrase": {"search_content": {"query": strict_keywords, "boost": 50.0}}},
                        {"match": {"search_content": {"query": strict_keywords, "fuzziness": "AUTO", "operator": "OR", "boost": 20.0}}},
                        {"query_string": {"query": wildcard_keywords, "default_field": "search_content", "default_operator": "OR", "boost": 5.0}}
                    ],
                    "minimum_should_match": 1
                }
            })

        # 4. INJECT NATIVE MATH REASONING
        for f in numeric_filters:
            field_name = f.get("field")
            operator = f.get("operator")
            val = f.get("value")
            
            if field_name and operator and val is not None:
                es_field = f"raw_payload.{field_name}" 
                
                if operator == "eq":
                    es_query_body["query"]["bool"]["filter"].append({"term": {es_field: val}})
                elif operator in ["gt", "gte", "lt", "lte"]:
                    es_query_body["query"]["bool"]["filter"].append({"range": {es_field: {operator: val}}})

        # 5. Execute the query
        search_res = await es.search(index=SINGLE_INDEX_NAME, body=es_query_body, _source_excludes=["search_content"], ignore_unavailable=True)
        
        # 🟢 THE RECURSIVE DEEP CLEAN FILTER
        garbage_keys = {"hf_context", "dataset_context", "ai_description", "ai_categories", "pubid"}
        
        def clean_payload_dict(raw_dict):
            cleaned = {}
            for k, v in raw_dict.items():
                if v in ["", None, [], {}]: continue
                if k in garbage_keys or str(k).startswith("Unnamed:"): continue
                
                # ALIGNMENT FIX: Crack open embedded Parquet JSON strings to expose the media_url for SAS token processing
                if isinstance(v, str) and '"embedded_parquet_media"' in v:
                    try:
                        parsed_v = json.loads(v)
                        if isinstance(parsed_v, dict):
                            v = parsed_v
                    except: pass
                
                if isinstance(v, str):
                    v = re.sub(r'(?i)\n*Question:\s*Finish your answer\.?', '', v).strip()
                    if v.lower() in ["finish your answer.", "continue.", "go on."]:
                        v = "[Conversation Continued]"
                    if not v: continue # Drop if it became empty after stripping
                    
                elif isinstance(v, dict):
                    v = clean_payload_dict(v)
                    if not v: continue # Drop nested dict if it became empty
                    
                elif isinstance(v, list):
                    new_list = []
                    for item in v:
                        if isinstance(item, dict):
                            cl_item = clean_payload_dict(item)
                            if cl_item: new_list.append(cl_item)
                        elif item not in ["", None, [], {}]:
                            new_list.append(item)
                    if not new_list: continue # Drop the entire list if it's now empty
                    v = new_list
                
                cleaned[k] = v
            return cleaned

        clean_results = []
        for hit in search_res["hits"]["hits"]:
            payload = hit["_source"].get("raw_payload", {})
            if payload.get("file_type") == "header":
                continue
                
            clean_item = clean_payload_dict(payload)
            if clean_item:
                clean_item["_source_domain"] = hit["_source"].get("domain")
                clean_results.append(clean_item)
        
        final_data = enrich_results_with_media(clean_results)[:limit]

        # ==========================================
        # CONTEXT-AWARE HEADER INJECTION
        # ==========================================
        def _check_for_text(payload):
            if isinstance(payload, dict):
                return any(_check_for_text(v) for k, v in payload.items() if k not in ['media_url', 'file_path', 'path', 'id', 'speaker_id'])
            elif isinstance(payload, list):
                return any(_check_for_text(v) for v in payload)
            elif isinstance(payload, str):
                if len(payload.split()) >= 4 and not payload.startswith(('http', 'hf://', '/', '\\')):
                    if not payload.strip().startswith('{'): 
                        return True
            return False

        has_text = _check_for_text(final_data)
        final_dump = json.dumps(final_data).lower()
        has_media = "media_url" in final_dump or "http" in final_dump or "data:" in final_dump or "hf://" in final_dump
        
        if final_data and has_media and not has_text:
            try:
                h_res = await es.search(
                    index=SINGLE_INDEX_NAME, 
                    body={
                        "query": {
                            "bool": {
                                "must": [
                                    {"term": {"domain": domain}},
                                    {"term": {"raw_payload.file_type.keyword": "header"}}
                                ]
                            }
                        }, 
                        "size": 1
                    }, 
                    _source_excludes=["search_content"], 
                    ignore_unavailable=True
                )
                if not h_res["hits"]["hits"]:
                    target_domain = final_data[0].get("_source_domain")
                    if target_domain and target_domain != domain:
                        h_res = await es.search(
                            index=SINGLE_INDEX_NAME, 
                            body={
                                "query": {
                                    "bool": {
                                        "must": [
                                            {"term": {"domain": target_domain}},
                                            {"term": {"raw_payload.file_type.keyword": "header"}}
                                        ]
                                    }
                                }, 
                                "size": 1
                            }, 
                            _source_excludes=["search_content"], 
                            ignore_unavailable=True
                        )

                h_hits = h_res["hits"]["hits"]
                if h_hits:
                    h_payload = h_hits[0]["_source"].get("raw_payload", {})
                    h_clean = clean_payload_dict(h_payload)
                    if h_clean:
                        h_clean["_source_domain"] = h_hits[0]["_source"].get("domain")
                        final_data.insert(0, h_clean)
            except Exception as e:
                logger.warning(f"Failed to fetch header for media-only response: {e}")

        return json.dumps(final_data)

    except Exception as e:
        logger.error(f"Search failed: {e}")
        return f"Error: {str(e)}"

# ==========================================
# SERVER ROUTING & DEPENDENCY FIX
# ==========================================
def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != EXPECTED_API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized Access")

app = FastAPI(dependencies=[Depends(verify_api_key)], root_path_in_servers=False)

class SearchRequest(BaseModel):
    domain: str
    query: str
    limit: int = 5

@app.post("/api/search")
async def standard_api_search(req: SearchRequest):
    logger.info(f"[REST API] Request received for domain: {req.domain}")
    try:
        result_string = await natural_language_search(domain=req.domain, natural_query=req.query, limit=req.limit)
        if result_string.startswith("Error:"):
            raise HTTPException(status_code=500, detail=result_string)
        
        # Optimized: Send string directly as JSON response to avoid redundant encode/decode latency
        return Response(content=result_string, media_type="application/json")
        
    except Exception as e:
        logger.error(f"[REST API] Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

app.mount("/", mcp.sse_app())