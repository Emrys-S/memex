FROM python:3.12-slim

WORKDIR /app

# CPU-only torch -- see zotero-search/Dockerfile, same reasoning: this NAS
# has no GPU, and the default `pip install torch` pulls the full CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    pyyaml \
    sentence-transformers \
    qdrant-client \
    anthropic \
    "mcp[cli]"

COPY vault_indexer.py query.py mcp_server.py ./

CMD ["python3", "-u", "vault_indexer.py"]
