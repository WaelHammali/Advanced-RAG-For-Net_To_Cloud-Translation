from __future__ import annotations

ROOT_DIR = "/kaggle/working/net2tf_v3"

EXTRACT_MODEL = "llama-3.3-70b-versatile"
PLAN_MODEL = "llama-3.3-70b-versatile"

KB_DIR = f"{ROOT_DIR}/kb"
INDEX_DIR = f"{ROOT_DIR}/index"
GENERATED_DIR = f"{ROOT_DIR}/generated"
TEMPLATES_DIR = f"{ROOT_DIR}/templates"

# Ansible paths
ANSIBLE_TEMPLATES_DIR = f"{ROOT_DIR}/ansible_templates"
ANSIBLE_GENERATED_DIR = f"{GENERATED_DIR}/ansible"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

TOP_K = 6
MAX_CHARS_PER_CHUNK = 1800
