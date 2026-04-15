FROM python:3.13-slim

WORKDIR /app

# System deps for lxml
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY app.py sync.py db.py queries.py ./
COPY parsers ./parsers
COPY templates ./templates
COPY static ./static

ENV POLITICS_DB=/data/data.sqlite
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
