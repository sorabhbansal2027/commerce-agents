# Commerce agents — Salesforce OMS API container.
# No existing source files were changed; this Dockerfile is the only addition needed
# to run the FastAPI on any cloud container service (ECS, Cloud Run, Railway, Render).
#
# Build:  docker build -t commerce-agent-api .
# Run:    docker run -p 8000:8000 \
#           -e SF_INSTANCE_URL=https://<org>.sandbox.my.salesforce.com \
#           -e SF_CLIENT_ID=... \
#           -e SF_CLIENT_SECRET=... \
#           -e ANTHROPIC_BASE_URL=http://litellm:4000 \
#           -e ANTHROPIC_API_KEY=sk-litellm-key \
#           commerce-agent-api

FROM python:3.11-slim

WORKDIR /app

# Copy everything first — local editable packages (commerce-common, etc.)
# must exist on disk before pip can install them.
# build: 2026-09-25
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# start.sh dispatches to the right process based on DEPLOY_MODE env var.
# DEPLOY_MODE=mcp → MCP server, DEPLOY_MODE=gemini → Gemini demo, default → storefront API.
RUN chmod +x start.sh
CMD ["./start.sh"]
