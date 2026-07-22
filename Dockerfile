FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git curl wget build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir semgrep

COPY . .

RUN mkdir -p reports vectordb/chroma_data security_rules training_data

RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn","app:fastapi_app","--host","0.0.0.0","--port","8000","--workers","2"]