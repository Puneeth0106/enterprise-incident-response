from pathlib import Path
import logging
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool


from src.models import ChatRequest, ChatResponse, UploadResponse
from src.self_rag import run_self_rag
from src.ingestion import ingest_file, namespace
from src.db import init_db, save_audit, latest_audits
from src.uploads import ingest_upload
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

ROOT = Path(__file__).resolve().parent
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)
logger = logging.getLogger(__name__)


app = FastAPI(
    title="CloudOps Sentinel: Enterprise Incident Response Self-RAG Copilot",
    version="2.0.0",
    description="Self-RAG copilot for cloud operations, production troubleshooting, and incident-response runbooks.",
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))



@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "cloudops-sentinel-self-rag"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    try:
        result = await run_in_threadpool(run_self_rag, payload.question.strip(), payload.thread_id.strip())
        response = ChatResponse(**result)
        await run_in_threadpool(save_audit, payload.question, result)
        return response
    except Exception:
        logger.exception("Chat request failed")
        raise HTTPException(status_code=500, detail="Unable to process the question") from None


@app.post("/api/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)):
    try:
        safe_name, count = await run_in_threadpool(
            ingest_upload, file.file, file.filename, UPLOADS, ingest_file
        )
        return UploadResponse(filename=safe_name, chunks_indexed=count, namespace=namespace())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Document ingestion failed")
        raise HTTPException(status_code=500, detail="Unable to ingest the document") from None
    finally:
        await file.close()


@app.get("/api/audits")
def audits(limit: int = 20):
    return latest_audits(min(max(limit, 1), 100))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
