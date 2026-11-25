FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home sentinel

COPY app.py data_ingestion.py ./
COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/
COPY documents/ ./documents/
COPY LICENSE SOURCE.md ./
RUN mkdir -p data uploads && chown sentinel:sentinel data uploads

USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
