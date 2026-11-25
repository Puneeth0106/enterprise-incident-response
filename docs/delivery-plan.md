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

## Progress

- Segment 1 merged in PR 1: bounded uploads, API checks and Python CI.
- Segment 2 merged in PR 2: real SQLite checkpoint regressions and deterministic workflow tests;
  follow-up retrieval uses the rewritten question, unsupported answers stop at the
  revision budget, zero disables web fallback, retry settings accept 0 through 5,
  and the graph step limit supports the full configured budget.
- Segment 3: real loader/splitter and index-boundary regressions, empty-document
  provider guard, restricted non-root image, hosted container startup/SQLite smoke
  and component diagram. Local Docker is unavailable; image validation runs in CI.
- After segment 3 passes review and hosted CI, this project is complete for this
  workflow stage at three PRs. Leave the fourth slot unused and select another
  academy project on the next run. Live providers, authentication, document version
  replacement/deletion, same-thread concurrency and deployment remain limitations.
