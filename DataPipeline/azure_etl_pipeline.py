import os, sys, time, json, base64, re, requests, logging, urllib.parse, mimetypes, math, threading
from azure.core.pipeline.transport import RequestsTransport
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pymongo import MongoClient
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient
from huggingface_hub import HfApi

# Force Python to load the system MIME database on slim images
# This ensures .wav, .mp3, etc., are recognized without hardcoding extensions.
mimetypes.init(files=["/etc/mime.types", "/etc/httpd/conf/mime.types"])

REPLICA_NAME = os.getenv("CONTAINER_APP_REPLICA_NAME", "local-0")
EXEC_ID = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME", "local-exec")

# ==========================================
# CLEAN LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [%(levelname)s] [{REPLICA_NAME}] %(message)s", stream=sys.stdout)
logger = logging.getLogger("LangScope_ETL")

# Silence noisy SDKs
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

class UniversalLangScopeETL:
    def __init__(self):
        logger.info(f"=== [BOOT] ETL Sync Engine | Exec: {EXEC_ID} ===")
        
        self.total_replicas = int(os.getenv("TOTAL_REPLICA_COUNT", 1))
        self.replicas_per_domain = int(os.getenv("REPLICAS_PER_DOMAIN", 10))
        
        self.max_bytes_per_dataset = float(os.getenv("MAX_GB_PER_DATASET", 120)) * 1024 * 1024 * 1024
        
        self.storage_conn = os.getenv("AZURE_STORAGE_CONN_STR")
        self.mongo_uri = os.getenv("MONGO_URI")
        self.hf_token = os.getenv("HF_TOKEN")
        
        if not self.storage_conn or not self.mongo_uri:
            logger.critical("Missing Azure/Mongo secrets!")
            sys.exit(1)
            
        self.db = MongoClient(self.mongo_uri)["langscope"]
        self.audit_db = self.db["etl_run_history"]
        # THE FIX: Explicitly mount a high-capacity adapter for Azure's Transport
        azure_session = requests.Session()
        azure_adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
        azure_session.mount("https://", azure_adapter)

        self.blob_service = BlobServiceClient.from_connection_string(
            self.storage_conn,
            max_single_put_connections=30,
            max_concurrency=50,  # (Also fixed from max_connections)
            transport=RequestsTransport(session=azure_session) # <--- Injecting the custom pool
        )
        self.cc = self.blob_service.get_container_client("raw-data")

        # Claim global ID and calculate squad identity
        self.global_replica_index = self._claim_replica_index()
        self.squad_id = self.global_replica_index // self.replicas_per_domain
        self.local_replica_index = self.global_replica_index % self.replicas_per_domain
        self.total_squads = math.ceil(self.total_replicas / self.replicas_per_domain)
        
        # Safely handle numbers that aren't clean multiples of 10
        self.actual_squad_size = sum(1 for i in range(self.total_replicas) if (i // self.replicas_per_domain) == self.squad_id)

        self.hf_api = HfApi(token=self.hf_token)
        self.session = requests.Session()
        retry_strategy = Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50))
        self.session.mount("http://", HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50))

    def _delete_blob(self, blob_name):
        try: self.cc.delete_blob(blob_name)
        except: pass
        
    def _run_background_gc(self):
        try:
            all_signals = [b.name for b in self.cc.list_blobs(name_starts_with="status_signals/")]
            old_blobs = [b for b in all_signals if not b.startswith(f"status_signals/{EXEC_ID}/")]
            if old_blobs:
                logger.info(f"[BACKGROUND GC] Purging {len(old_blobs)} orphaned files...")
                with ThreadPoolExecutor(max_workers=10) as ex:
                    list(ex.map(self._delete_blob, old_blobs))
                logger.info("[BACKGROUND GC] Complete.")
        except Exception as e:
            logger.warning(f"Background GC failed: {e}")
            
    def _claim_replica_index(self):
        # Claim global ID logic - GC has been moved to a background thread
        for attempt in range(3):
            for idx in range(self.total_replicas):
                # Write locks into the isolated execution folder
                blob = self.cc.get_blob_client(f"status_signals/{EXEC_ID}/system_locks/replica_{idx}.lock")
                try:
                    blob.upload_blob(REPLICA_NAME.encode(), overwrite=False)
                    logger.info(f"Successfully claimed Global Index: {idx}")
                    return idx
                except Exception as e:
                    if "BlobAlreadyExists" in str(e): continue
            time.sleep(3)
            
        logger.critical("Could not claim a unique index! Exiting to prevent squad corruption.")
        sys.exit(1)

    @staticmethod
    def is_harmful(filename):
        """Dynamic Blacklist: Blocks scripts, executables, and heavy unindexable model tensors."""
        harmful_exts = {
            # Executables
            '.exe', '.dll', '.so', '.sh', '.bat', '.cmd', '.msi', '.app', 
            '.scr', '.vbs', '.cpl', '.pif', '.jar', '.apk', '.pyc', '.php', '.pl', '.rb',
            '.npy', '.safetensors', '.bin', '.h5', '.ckpt', '.pt', '.pth', '.msgpack', '.onnx', '.tflite', '.pb', '.npz'
        }
        return any(filename.lower().endswith(ext) for ext in harmful_exts)

    def sanitize_name(self, name):
        return re.sub(r'[^a-zA-Z0-9]', '_', name.lower()).strip('_')

    def get_last_indexed_date(self, s_path):
        """Returns precise UTC datetime object instead of a stripped date"""
        try:
            blob = self.cc.get_blob_client(f"raw/{s_path}/indexing.done")
            if blob.exists(): 
                return blob.get_blob_properties().last_modified.astimezone(timezone.utc)
        except: pass
        return None

    def get_remote_date(self, url):
        """Handles both String and Datetime objects from HF API"""
        try:
            if "huggingface.co" in url:
                repo_id = url.split("datasets/")[-1].split("?")[0].split("/tree/")[0].strip("/")
                dt_val = self.hf_api.dataset_info(repo_id).lastModified
                
                # Handle new SDK behavior (Datetime object)
                if isinstance(dt_val, datetime):
                    return dt_val.astimezone(timezone.utc) if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)
                
                # Handle legacy SDK behavior (String)
                elif isinstance(dt_val, str):
                    dt_str = dt_val.replace('Z', '+00:00')
                    try: 
                        return datetime.fromisoformat(dt_str).astimezone(timezone.utc)
                    except ValueError: 
                        parsed = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S")
                        return parsed.replace(tzinfo=timezone.utc)
                        
            elif url.startswith("http"):
                resp = self.session.head(url, allow_redirects=True, timeout=15)
                if "Last-Modified" in resp.headers: 
                    return parsedate_to_datetime(resp.headers["Last-Modified"]).astimezone(timezone.utc)
        except Exception as e:
            logger.warning(f"Remote date check failed (Forcing sync): {e}")
        return datetime.now(timezone.utc)

    def wait_for_cleanup(self, domain, s_path):
        # Isolated cleanup signal
        signal_blob = f"status_signals/{EXEC_ID}/{s_path}/cleanup.done"

        if self.local_replica_index == 0:
            logger.info(f"[Squad {self.squad_id}] Purging {domain} landing zone (Production Batch Mode)...")
            
            # PRODUCTION UPGRADE: Paginated Native Batch Deletion
            try:
                generator = self.cc.list_blobs(name_starts_with=f"raw/{s_path}/", results_per_page=5000).by_page()
                for page in generator:
                    batch = [b.name for b in page]
                    if not batch: continue
                    
                    chunk_size = 256
                    for i in range(0, len(batch), chunk_size):
                        sub_batch = batch[i:i+chunk_size]
                        for attempt in range(3):
                            try:
                                self.cc.delete_blobs(*sub_batch)
                                break
                            except Exception as e:
                                logger.warning(f"Batch deletion failed (Attempt {attempt+1}/3). Retrying... Error: {e}")
                                time.sleep(3)
            except Exception as e:
                logger.error(f"Failed during cleanup phase for {domain}: {e}")
            
            self.cc.get_blob_client(signal_blob).upload_blob(b"OK", overwrite=True)

        start_time = time.monotonic()
        while not self.cc.get_blob_client(signal_blob).exists():
            if time.monotonic() - start_time > 600:
                logger.error(f"[Squad {self.squad_id}] Cleanup timeout! Proceeding aggressively.")
                break
            time.sleep(5)

    def download_file(self, dl_url, b_path, max_retries=3):
        blob_client = self.cc.get_blob_client(b_path)
        
        # DYNAMIC HEADERS: Only send HF token to Hugging Face domains
        req_headers = {}
        parsed_url = urllib.parse.urlparse(dl_url)
        if self.hf_token and parsed_url.netloc.endswith(("huggingface.co", "hf.co")):
            req_headers["Authorization"] = f"Bearer {self.hf_token}"
        
        for attempt in range(max_retries):
            try:
                logger.info(f"[Squad {self.squad_id}] Downloading: {b_path} (Attempt {attempt + 1}/{max_retries})")
                
                with self.session.get(dl_url, stream=True, timeout=60, headers=req_headers) as r:
                    if r.status_code == 404:
                        logger.warning(f"Skipping missing file (404): {dl_url}")
                        return False
                    
                    # EXPONENTIAL BACKOFF: Handle rate limits safely
                    if r.status_code in (429, 503, 504):
                        sleep_time = (2 ** attempt) * 5 
                        logger.warning(f"Source throttled us ({r.status_code}). Sleeping for {sleep_time}s...")
                        time.sleep(sleep_time)
                        continue
                    
                    r.raise_for_status()
                    
                    def stream_generator():
                        # MULTI-THREADING TWEAK: 16MB chunks balance RAM and HTTP overhead
                        for chunk in r.iter_content(chunk_size=16 * 1024 * 1024): 
                            if chunk: yield chunk
                    
                    # MULTI-THREADING TWEAK: Max concurrency parallelizes upload to Azure
                    blob_client.upload_blob(
                        stream_generator(), 
                        overwrite=True,
                        max_concurrency=5
                    )
                return True
                
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed download {b_path} after {max_retries} attempts: {e}")
                    raise e
                
                sleep_time = (2 ** attempt) * 5
                logger.warning(f"Stream interrupted for {b_path}. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
        return False

    def signal_and_wait(self, domain, s_path):
        # Isolated DL signals
        self.cc.get_blob_client(f"status_signals/{EXEC_ID}/{s_path}/dl_squad_{self.squad_id}_rep_{self.local_replica_index}.done").upload_blob(b"OK", overwrite=True)
        
        logger.info(f"Replica {self.local_replica_index} finished downloading {domain}!")
        logger.info(f"Replica {self.local_replica_index} waiting for squad {self.squad_id} to sync...")
        start_time = time.monotonic()
        
        while True:
            done_count = sum(1 for _ in self.cc.list_blobs(name_starts_with=f"status_signals/{EXEC_ID}/{s_path}/dl_squad_{self.squad_id}_"))
            
            if done_count >= self.actual_squad_size: 
                if self.local_replica_index == 0:
                    logger.info(f"ALL {self.actual_squad_size} REPLICAS in Squad {self.squad_id} have finished {domain}!")
                break
                
            if time.monotonic() - start_time > 300: 
                if self.local_replica_index == 0:
                    logger.warning(f"Squad {self.squad_id} sync timeout. Proceeding with {done_count} replicas.")
                break
            time.sleep(10)

    def process_domain(self, doc):
        domain = doc["Domain"]
        
        data_sources = doc.get("Data_Sources", [])
        if not data_sources and doc.get("Data Source URL"):
            data_sources = [doc.get("Data Source URL")]
            
        # Extract the fields
        task_type = doc.get("Task_Type", "Unknown")
        parent_cat = doc.get("Parent_Task_Category", "Unknown")
            
        s_path = self.sanitize_name(domain)
        domain_success = False
        
        # Using a map for Context to segregate multiple datasets in one domain queue payload
        hf_context_map = {} 
        
        logger.info(f"[Squad {self.squad_id}] Checking MongoDB... Domain: [{domain}] | Sources: {len(data_sources)}")

        # ==========================================
        # PER-SOURCE DELTA LOGIC
        # ==========================================
        last_idx = self.get_last_indexed_date(s_path)
        urls_to_process = []
        
        if not last_idx:
            logger.info(f"No indexing.done found for {domain}. All {len(data_sources)} sources will be downloaded.")
            urls_to_process = data_sources
        else:
            for url in data_sources:
                rem_date = self.get_remote_date(url)
                if rem_date > last_idx:
                    logger.info(f"Source Updated -> {url} (Remote: {rem_date} > Local: {last_idx}). Queued for sync.")
                    urls_to_process.append(url)
                else:
                    logger.info(f"Source Current -> {url} is up to date. Skipping.")

        # Skip the domain entirely if no sources need updating
        if not urls_to_process:
            logger.info(f"All sources for {domain} are up to date. Skipping domain.")
            if self.local_replica_index == 0: 
                self.db["ground_truth"].update_one({"_id": doc["_id"]}, {"$set": {"status": "SYNCED"}})
                self.audit_db.update_one(
                    {"_id": EXEC_ID},
                    {
                        "$set": {f"domains.{domain}.status": "SKIPPED", f"domains.{domain}.end_time": datetime.now(timezone.utc)},
                        "$setOnInsert": {"start_time": datetime.now(timezone.utc)}
                    },
                    upsert=True
                )
            return

        try:
            # Clean landing zone before dumping new data
            self.wait_for_cleanup(domain, s_path)
            
            # ---> ADD THIS FIX: Explicitly set the status to SYNCING <---
            if self.local_replica_index == 0:
                self.audit_db.update_one(
                    {"_id": EXEC_ID},
                    {
                        "$set": {f"domains.{domain}.status": "SYNCING", "last_updated": datetime.now(timezone.utc)},
                        "$setOnInsert": {"start_time": datetime.now(timezone.utc)}
                    },
                    upsert=True
                )

            # Loop sequentially through the URLs that need updating
            for idx, url in enumerate(urls_to_process):
                logger.info(f"[Squad {self.squad_id}] Processing URL {idx+1}/{len(urls_to_process)}: {url}")

                if "huggingface.co" in url:
                    repo_id = url.split("datasets/")[-1].split("?")[0].split("/tree/")[0].strip("/")
                    target = url.split("/tree/main/")[-1] if "/tree/main/" in url else None
                    
                    logger.info(f"Target Hugging Face Repo: {repo_id}")
                    
                    try:
                        dataset_info = self.hf_api.dataset_info(repo_id)
                        
                        desc = getattr(dataset_info, 'description', '')
                        repo_description = (desc if desc else '')[:2000]
                        
                        tags = getattr(dataset_info, 'tags', [])
                        repo_tags = [str(t) for t in tags] if tags else []
                        
                        hf_context = f"Source Dataset: {repo_id}. Tags: {', '.join(repo_tags)}. Description: {repo_description}".replace("\n", " ").strip()
                        hf_context_map[repo_id] = hf_context
                    except Exception as e:
                        logger.warning(f"Could not fetch rich metadata: {e}")
                    
                    manifest_blob = f"status_signals/{EXEC_ID}/{s_path}/manifest_{repo_id.replace('/', '_')}.json"
                    
                    if self.local_replica_index == 0:
                        logger.info(f"[Squad {self.squad_id}] Replica 0 is compiling file manifest for {repo_id}. This prevents API throttling...")
                        all_files_data = []
                        try:
                            # Using a generator avoids RAM bloat during the fetch
                            for f in self.hf_api.list_repo_tree(repo_id=repo_id, repo_type="dataset", recursive=True):
                                # Immediately drop empty files, harmful executables, and millions of useless .npy tensors
                                if getattr(f, "size", 0) == 0: continue
                                if target and not f.path.startswith(f"{target}/"): continue
                                if any(b in f.path for b in [".gitattributes", "LICENSE", ".gitignore"]): continue
                                if self.is_harmful(f.path): continue
                                
                                all_files_data.append({"path": f.path, "size": getattr(f, "size", 0)})
                                
                        except Exception as hf_err:
                            logger.error(f"Failed to build tree for {repo_id}. HF API Error: {hf_err}")
                            raise hf_err
                            
                        self.cc.get_blob_client(manifest_blob).upload_blob(json.dumps(all_files_data).encode('utf-8'), overwrite=True)
                        logger.info(f"[Squad {self.squad_id}] Manifest created with {len(all_files_data)} valid files.")

                    # All replicas wait for the manifest to be published
                    logger.info(f"[Squad {self.squad_id}] Replica {self.local_replica_index} waiting for manifest sync...")
                    wait_start = time.monotonic()
                    while not self.cc.get_blob_client(manifest_blob).exists():
                        if time.monotonic() - wait_start > 3600: # 1 hour max wait for giant repos
                            raise Exception("Timeout waiting for Replica 0 to publish dataset manifest.")
                        time.sleep(10)
                        
                    # Load the filtered manifest
                    manifest_bytes = self.cc.get_blob_client(manifest_blob).download_blob().readall()
                    all_files_data = json.loads(manifest_bytes.decode('utf-8'))
                    
                    # Convert to minimal object to preserve existing downstream parallelization logic
                    class DummyFile:
                        def __init__(self, p, s): self.path = p; self.size = s
                        
                    valid_targets = []
                    c_size = 0
                    
                    for d in all_files_data:
                        f = DummyFile(d["path"], d["size"])
                        mime_type, _ = mimetypes.guess_type(f.path)
                        is_media = mime_type is not None and mime_type.startswith(('image/', 'video/', 'audio/'))
                        
                        if c_size + f.size > self.max_bytes_per_dataset:
                            logger.warning(f"Size limit ({self.max_bytes_per_dataset/1e9}GB) reached for dataset {repo_id}.")
                            break
                            
                        c_size += f.size
                        valid_targets.append((f, is_media))
                    
                    assigned = [item for i, item in enumerate(valid_targets) if i % self.actual_squad_size == self.local_replica_index]
                    logger.info(f"[Squad {self.squad_id}] Found {len(valid_targets)} files. Local Replica {self.local_replica_index} handling {len(assigned)} files.")
                    
                    media_metadata_list = []
                    meta_lock = threading.Lock()
                    
                    def process_hf_item(item):
                        f, is_media = item
                        hf_url = f"https://huggingface.co/datasets/{repo_id}/resolve/main/{f.path}"
                        f_size = getattr(f, "size", 0)
                        
                        if is_media:
                            meta_payload = {
                                "file_type": "direct_hf_media",
                                "file_path": f.path,
                                "media_url": hf_url,
                                "size": f_size,
                                "dataset_context": hf_context_map.get(repo_id, "") 
                            }
                            with meta_lock:
                                media_metadata_list.append(meta_payload)
                            return True, f_size
                        else:
                            # Added repo_id to file path to prevent collision
                            safe_repo = repo_id.replace('/', '_')
                            safe_path = f.path.replace('/', '_')
                            success = self.download_file(hf_url, f"raw/{s_path}/hf_{safe_repo}_{safe_path}")
                            return success, f_size if success else 0
                    
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        results = list(executor.map(process_hf_item, assigned))
                    
                    success_count = sum(1 for r, _ in results if r)
                    fail_count = len(results) - success_count
                    total_bytes = sum(s for r, s in results if r)
                    
                    if results:
                        self.audit_db.update_one(
                            {"_id": EXEC_ID},
                            {
                                "$inc": {
                                    f"domains.{domain}.files_success": success_count,
                                    f"domains.{domain}.files_failed": fail_count,
                                    f"domains.{domain}.total_bytes": total_bytes
                                },
                                "$set": {"last_updated": datetime.now(timezone.utc)},
                                "$setOnInsert": {"start_time": datetime.now(timezone.utc)}
                            },
                            upsert=True
                        )
                        
                    if media_metadata_list:
                        safe_repo = repo_id.replace('/', '_')
                        meta_path = f"raw/{s_path}/hf_media_pointers_{safe_repo}_rep_{self.local_replica_index}.jsonl"
                        jsonl_content = "\n".join([json.dumps(m) for m in media_metadata_list])
                        self.cc.get_blob_client(meta_path).upload_blob(jsonl_content.encode('utf-8'), overwrite=True)
                        logger.info(f"   -> Created metadata JSONL for {repo_id}")

                elif url.startswith("http") and self.local_replica_index == 0:
                    resp = self.session.head(url, allow_redirects=True, timeout=15)
                    file_name = None
                    
                    if "Content-Disposition" in resp.headers:
                        match = re.search(r'filename=["\']?([^";]+)["\']?', resp.headers["Content-Disposition"])
                        if match: file_name = match.group(1)
                    
                    if not file_name:
                        parsed_url = urllib.parse.urlparse(resp.url)
                        file_name = os.path.basename(parsed_url.path)
                    
                    if not file_name or file_name == "":
                        content_type = resp.headers.get('Content-Type', '').split(';')[0]
                        ext = mimetypes.guess_extension(content_type) or '.bin'
                        file_name = f"dynamic_download_{idx}{ext}"

                    file_name = os.path.basename(file_name)
                    content_length = int(resp.headers.get('Content-Length', 0))
                    
                    hf_context_map[file_name] = f"Source URL: {url}"

                    target_b_path = f"raw/{s_path}/{file_name}"
                    file_success = False
                    skip_download = False

                    # ---> FIX: Smart README Overwrite Protection <---
                    if file_name.lower() == "readme.md":
                        bc = self.cc.get_blob_client(target_b_path)
                        if bc.exists():
                            existing_size = bc.get_blob_properties().size
                            if content_length <= existing_size:
                                logger.info(f"[SMART OVERWRITE] Preserving existing {file_name} ({existing_size} bytes) over new smaller one ({content_length} bytes).")
                                skip_download = True
                                file_success = True  # Mark as success so we don't fail the audit tally

                    if not skip_download:
                        if self.is_harmful(file_name):
                            logger.warning(f"Blocked harmful/executable download: {file_name}")
                        elif content_length > self.max_bytes_per_dataset:
                            logger.warning(f"HTTP Download size ({content_length/1e9}GB) exceeds dataset limits. Aborting.")
                        else:
                            file_success = self.download_file(url, target_b_path)

                    self.audit_db.update_one(
                        {"_id": EXEC_ID},
                        {
                            "$inc": {
                                f"domains.{domain}.files_success": 1 if file_success else 0,
                                f"domains.{domain}.files_failed": 0 if file_success else 1,
                                f"domains.{domain}.total_bytes": content_length if file_success else 0
                            },
                            "$set": {"last_updated": datetime.now(timezone.utc)},
                            "$setOnInsert": {"start_time": datetime.now(timezone.utc)}
                        },
                        upsert=True
                    )

            domain_success = True

        except Exception as e:
            logger.error(f"Domain {domain} failed: {e}")
            if self.local_replica_index == 0:
                self.db["ground_truth"].update_one({"_id": doc["_id"]}, {"$set": {"status": "FAILED_SYNC"}})
            
            try:
                err_path = f"status_signals/{EXEC_ID}/errors/{s_path}/etl_replica_{self.local_replica_index}.log"
                self.cc.get_blob_client(err_path).upload_blob(f"ETL Failure: {str(e)}".encode('utf-8'), overwrite=True)
            except: pass
        
        finally:
            self.signal_and_wait(domain, s_path)
            
            if self.local_replica_index == 0:
                if domain_success:
                    try:
                        queue_client = QueueClient.from_connection_string(self.storage_conn, queue_name="indexer-trigger-queue")
                        payload = json.dumps({
                            "domain": domain, 
                            "s_path": s_path, 
                            "reset": False, 
                            "hf_context_map": hf_context_map, 
                            "task_type": task_type,
                            "parent_category": parent_cat,
                            "exec_id": EXEC_ID
                        })
                        queue_client.send_message(base64.b64encode(payload.encode('utf-8')).decode('utf-8'))
                        self.db["ground_truth"].update_one({"_id": doc["_id"]}, {"$set": {"status": "SYNCED", "last_sync": datetime.now(timezone.utc)}})
                        logger.info(f"Signaled Indexer for {domain}")
                        
                        self.audit_db.update_one(
                            {"_id": EXEC_ID},
                            {"$set": {f"domains.{domain}.status": "COMPLETED", f"domains.{domain}.end_time": datetime.now(timezone.utc)}}
                        )
                    except Exception as e: 
                        logger.error(f"Queue signaling failed: {e}")
                else:
                    try:
                        self.audit_db.update_one(
                            {"_id": EXEC_ID},
                            {"$set": {f"domains.{domain}.status": "FAILED", f"domains.{domain}.end_time": datetime.now(timezone.utc)}}
                        )
                    except: pass

    def run(self):
        if self.global_replica_index == 0:
            logger.info("Leader Pod (Replica 0) spawning background GC thread...")
            threading.Thread(target=self._run_background_gc, daemon=True).start()
            
        env_raw = os.getenv("REFRESH_DOMAINS")
        if not env_raw: return
        domains = [d.strip().strip("'").strip('"') for d in env_raw.split(",")]
        
        my_domains = [d for i, d in enumerate(domains) if i % self.total_squads == self.squad_id]
        
        if not my_domains:
            logger.info(f"Squad {self.squad_id} has no domains assigned. Sleeping.")
            return
            
        logger.info(f"Squad {self.squad_id} (Size: {self.actual_squad_size}) assigned to domains: {my_domains}")

        docs = {doc["Domain"]: doc for doc in self.db["ground_truth"].find({"Domain": {"$in": my_domains}})}
        
        for d in my_domains:
            if d in docs:
                self.process_domain(docs[d])

if __name__ == "__main__":
    UniversalLangScopeETL().run()