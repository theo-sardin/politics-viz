FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY pv ./pv
RUN pip install --no-cache-dir .

# Raw downloads, the database and the legacy archive all live under /data.
ENV PV_DATA_DIR=/data
VOLUME /data
EXPOSE 8000

# Build the database first:  docker run --rm -v $PWD/data:/data politics-viz pv sync
CMD ["uvicorn", "pv.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
