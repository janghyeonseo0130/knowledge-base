from __future__ import annotations
from pathlib import Path
import yaml

_CFG_PATH = Path(__file__).parent / "config.yaml"

with open(_CFG_PATH, encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f)

BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / _cfg["paths"]["data_dir"]
CHROMA_PATH = BASE_DIR / _cfg["paths"]["chroma_dir"]
BM25S_PATH  = BASE_DIR / _cfg["paths"]["bm25s_path"]
DB_PATH     = BASE_DIR / _cfg["paths"]["tracker_db"]
UPLOADS_DIR = Path(_cfg["paths"]["uploads_dir"])

EMBEDDING_MODEL      = _cfg["embedding"]["model"]
EMBEDDING_DIM        = _cfg["embedding"]["dim"]
EMBEDDING_BATCH_SIZE = _cfg["embedding"]["batch_size"]

CHUNK_SIZE               = _cfg["chunking"]["size"]
CHUNK_OVERLAP            = _cfg["chunking"]["overlap"]
STRUCTURE_SPLIT_MIN_SIZE = _cfg["chunking"]["structure_split_min_size"]
MIN_BODY_SIZE            = _cfg["chunking"]["min_body_size"]
MAX_CHUNKS_PER_FILE           = _cfg["chunking"].get("max_chunks_per_file")
TABLE_FULL_INDEX_CHUNK_LIMIT  = _cfg.get("table", {}).get("full_index_chunk_limit")

TOP_K_VECTOR = _cfg["search"]["vector_top_k"]
TOP_K_BM25   = _cfg["search"]["bm25_top_k"]
RRF_K        = _cfg["search"]["rrf_k"]

USE_RERANKER   = _cfg["reranker"]["enabled"]
RERANKER_MODEL = _cfg["reranker"]["model"]
TOP_K_RERANK   = _cfg["reranker"]["top_k"]

MCP_HOST = _cfg["mcp"]["host"]
MCP_PORT = _cfg["mcp"]["port"]

WATCHER_DEBOUNCE          = _cfg["watcher"]["debounce_seconds"]
REBUILD_DEBOUNCE_MINUTES  = _cfg["bm25"]["rebuild_debounce_minutes"]

OCR_ENABLED        = _cfg["ocr"]["enabled"]
OCR_GPU            = _cfg["ocr"]["gpu"]
OCR_MIN_TEXT_CHARS = _cfg["ocr"]["min_text_chars"]
OCR_LANGUAGES      = _cfg["ocr"]["languages"]

SUPPORTED_EXTENSIONS = set(_cfg["supported_extensions"])


def get_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
