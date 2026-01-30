FROM python:3.11-slim

RUN apt-get update && apt-get install -y build-essential libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info curl && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml ./

RUN uv pip compile pyproject.toml -o requirements.txt && uv pip install --system --no-cache -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PORT=3456

EXPOSE 3456

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3456"]
