# Commerce agents — retail API container.
# No existing source files were changed; this Dockerfile is the only addition needed
# to run the FastAPI on any cloud container service (ECS, Cloud Run, Railway, Render).
#
# Build:  docker build -t commerce-agent-api .
# Run:    docker run -p 8000:8000 \
#           -e ANTHROPIC_BASE_URL=http://litellm:4000 \
#           -e ANTHROPIC_API_KEY=sk-litellm-key \
#           commerce-agent-api

FROM python:3.11-slim

WORKDIR /app

# Copy everything first — local editable packages (commerce-common, etc.)
# must exist on disk before pip can install them.
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# --app-dir adds examples/ to sys.path so "retail.api.main" resolves correctly.
CMD ["uvicorn", "retail.api.main:app", \
     "--app-dir", "examples", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
