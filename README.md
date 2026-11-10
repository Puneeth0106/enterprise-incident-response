# CloudOps Sentinel: Enterprise Incident Response Self-RAG Copilot

An AI copilot for investigating cloud incidents with operational runbooks, corrective retrieval, and persistent conversation context. The application combines **LangGraph**, **Pinecone**, **OpenAI**, and **FastAPI** to turn incident questions into answers grounded in retrieved evidence.

## Engineering capabilities

- **Self-correcting retrieval:** grade document relevance, rewrite weak queries, and evaluate answer support and usefulness.
- **Knowledge ingestion:** load PDF, TXT, Markdown, and DOCX files, split them into chunks, and index them with stable identifiers.
- **Stateful workflows:** preserve incident context across follow-up questions with LangGraph and SQLite checkpoints.
- **Search fallback:** use Tavily when internal retrieval does not provide sufficient evidence.
- **Application integration:** connect chat, document uploads, audit history, and a browser interface through FastAPI.
- **Deployment packaging:** provide a Dockerfile and environment-based service configuration.

## Architecture

```text
User Incident / Follow-up
        ↓
LangGraph SQLite Memory
        ↓
Contextualize Follow-up
        ↓
Decide Retrieval
        ↓
Private Pinecone Knowledge Base
        ↓
Grade Retrieved Documents
   ┌────┴───────────────┐
Relevant             Weak / Missing
   ↓                      ↓
Generate              Rewrite Query
   ↓                      ↓
IsSUP              Retry Private KB
   ↓                      ↓
Revise if needed   Internet Search Fallback
   ↓                      ↓
IsUSE              Grade Web Evidence
   ↓                      ↓
Final Answer ← Generate → IsSUP → IsUSE
        ↓
SQLite Checkpoint / Memory
```

## Tech stack

- **LangGraph**: Self-RAG workflow, conditional routing, persistent thread state
- **OpenAI `gpt-5-mini`**: routing, query rewriting, generation, relevance grading, IsSUP and IsUSE
- **OpenAI `text-embedding-3-large`**: embeddings for private operational documents
- **Pinecone**: private runbooks/SOPs/postmortems knowledge base
- **Tavily**: controlled internet-search fallback
- **SQLite**: LangGraph persistence memory + incident audit storage
- **FastAPI**: application/API backend
- **HTML/CSS/JavaScript**: incident-command-center UI with document upload
- **Docker**: deployment packaging

## Project structure

```text
CloudOps-Sentinel-Enterprise-Incident-Response-Self-RAG-Copilot/
├── app.py
├── data_ingestion.py          # ONE file to build the Pinecone KB
├── src/
│   ├── config.py
│   ├── db.py
│   ├── ingestion.py
│   ├── models.py
│   ├── self_rag.py
│   └── vectorstore.py
├── documents/                 # initial private knowledge documents
│   ├── checkout-api-runbook.md
│   ├── payments-high-cpu-runbook.md
│   └── deployment-rollback-sop.md
├── templates/index.html
├── static/styles.css
├── static/app.js
├── uploads/                   # documents uploaded through the UI
├── data/                      # SQLite persistence files
├── requirements.txt
├── Dockerfile
└── .env.example
```

## 1. Setup

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

macOS/Linux:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure:

```env
OPENAI_API_KEY=...
PINECONE_API_KEY=...
TAVILY_API_KEY=...
```

The defaults are:

```env
OPENAI_MODEL=gpt-5-mini
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSION=3072
PINECONE_INDEX_NAME=cloudops-sentinel-openai-self-rag
PINECONE_NAMESPACE=incident-runbooks
```

> The project intentionally uses a new default Pinecone index name. The earlier local embedding version used a different vector dimension, and Pinecone index dimensions cannot be mixed.

## 2. Build the entire Pinecone knowledge base with ONE command

Put your initial PDF/TXT/MD/DOCX documents inside `documents/`, then run:

```bash
python data_ingestion.py
```

That one file performs the complete ingestion pipeline:

```text
Read .env
  ↓
Create / validate Pinecone index
  ↓
Load ./documents
  ↓
Split documents into chunks
  ↓
OpenAI text-embedding-3-large
  ↓
Upsert to Pinecone
  ↓
Configured namespace is ready
```

The ingestion uses stable chunk IDs, so rerunning the script updates matching vectors rather than blindly generating a new random ID for every chunk.

## 3. Run the application

Either:

```bash
python app.py
```

or:

```bash
uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Add new documents from the UI

The left-side **Runbook Vault** accepts:

- PDF
- TXT
- Markdown
- DOCX

When you click **Add to knowledge base**, the backend uses the exact same ingestion layer and OpenAI embedding model as `data_ingestion.py`.

```text
New UI Document
      ↓
FastAPI /api/upload
      ↓
Document Loader + Chunking
      ↓
OpenAI Embeddings
      ↓
EXISTING Pinecone Index
      ↓
EXISTING Namespace
```

New runbooks can be added through the interface without using a separate ingestion workflow.

## LangGraph SQLite persistence memory

Every browser incident session has a persistent `thread_id`. The LangGraph workflow is compiled with a SQLite checkpointer and stores checkpoints in:

```text
data/langgraph_memory.sqlite
```

Example incident conversation:

**Question 1**

> Our checkout API is returning 502 errors after deployment. What should I check first?

**Question 2**

> What should I check next if that doesn't work?

For the second message, the `contextualize` node uses the persisted incident context and converts the follow-up into a standalone operational question before continuing through Self-RAG.

SQLite provides local persistence. A production deployment would require a separate review of storage, concurrency, authentication, and operational controls.

## Example workflows

### A: Existing Pinecone knowledge

Ask:

> Our checkout API is returning 502 errors after deployment. What should the on-call engineer check first?

Expected route: **Private Runbooks**.

### B: Persistent memory

Immediately ask:

> What should I check next if that does not work?

The same `thread_id` should reuse the previous incident context.

### C: Upload new operational knowledge

Upload a new PDF/MD/DOCX from the UI. The status will show how many chunks were added to the existing namespace. Then ask a question whose answer exists only in the newly uploaded document.

### D: Internet-search fallback

Ask about a technical issue that is not covered by any private runbook. Self-RAG first tries the private KB, grades the evidence, rewrites if necessary, and then transitions to **Internet Search** when internal evidence remains insufficient.

## Important configuration rule

The application and ingestion script both import `src/vectorstore.py`. That means they always use the same:

- OpenAI embedding model
- embedding dimension
- Pinecone index
- Pinecone namespace

This prevents a common RAG mistake where offline ingestion and runtime retrieval use different embeddings or different namespaces.

## Validation status

Python syntax and notebook JSON checks passed. Live API calls, ingestion, Docker execution, and end-to-end behavior have not yet been verified.

I learned from Bappy Ahmed, whose original work is MIT-licensed, through Krish Naik Academy.
