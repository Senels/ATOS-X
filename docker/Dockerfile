FROM python:3.13-slim

WORKDIR /app

COPY backend/pyproject.toml /app/pyproject.toml
COPY backend/app /app/app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
