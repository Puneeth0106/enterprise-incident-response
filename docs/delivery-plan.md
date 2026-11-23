# Delivery plan

This workflow allows at most four opened pull requests, including closed PRs.
Three segments are planned. Stop earlier when the project is ready.
The existing academy import and its license/source attribution are preserved.

1. **Bounded upload and API boundary:** isolate uploaded documents, cap staging
   bytes, clean up success/failure paths, reject blank chat input, hide backend
   errors from clients, and add credential-free API/SQLite tests and Python CI.
2. **Workflow persistence and routing:** verify conversation checkpoints,
   follow-up context, retry limits and evidence routing with deterministic model
   doubles and real disposable SQLite; fix demonstrated runtime issues together.
3. **Ingestion and packaging:** verify loaders, chunk identity and indexing
   failures with service doubles, validate the container startup path, and finish
   reproduction docs and a code-grounded diagram if it adds useful detail.

## Scope and limits

This is a local copilot without authentication or user isolation. No segment
claims production readiness. Live OpenAI, Pinecone and Tavily validation requires
configured services and is outside credential-free checks. Request staging
limits do not bound multipart parsing, extracted document size, provider writes,
or concurrent request count. The deployment proxy must bound request bodies.

Before each PR, inspect remote main, all PR states and merged branches. Record
opened PR count, verification and remaining scope in the automation ledger and
brain project index. Never open a fifth workflow PR.
