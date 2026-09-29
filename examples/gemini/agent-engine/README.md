# Gemini Merchant Agent — Path 3: Vertex AI Agent Engine

Managed hosting for the Gemini ADK merchant agent. This is the fully-hosted
path where Google manages the infrastructure, equivalent to
`merchant-agent/managed-agents/` in the Claude merchant agent path.

The `app.py` module wraps the ADK agent (built by `examples/gemini/api/agent.py`)
in `vertexai.preview.reasoning_engines.AdkApp`. `deploy.py` is the one-command
CLI for pushing that wrapper to Vertex AI Agent Engine and tearing it down.
`session_service.py` provides a SQLite-backed session service for local and
self-hosted deployments that need sessions to survive process restarts.

## Deployment options

### 1. Local testing (no GCP)

No `vertexai` package needed. `create_merchant_app` falls back to a local ADK
runner backed by `InMemorySessionService`.

```bash
pip install google-adk>=2.10.0
export GOOGLE_API_KEY=AIza...        # Google AI Studio key
export SFCC_INSTANCE_URL=https://...  # optional; required for live data
```

```python
from examples.gemini.agent_engine.app import create_merchant_app

app = create_merchant_app("DreamHaus")
result = app.query(input="What needs my attention today?", session_id="dev-1")
print(result["output"])
```

### 2. Self-hosted (SQLite session persistence)

Use `SQLiteSessionService` instead of the in-memory default when you run the
agent on your own server and need sessions to survive restarts.

```python
from google.adk.runners import Runner
from examples.gemini.api.agent import _build_agent
from examples.gemini.agent_engine.session_service import SQLiteSessionService

session_service = SQLiteSessionService(db_path=".sessions.db")
runner = Runner(
    agent=_build_agent("DreamHaus"),
    app_name="gemini_merchant",
    session_service=session_service,
)
```

### 3. Google Cloud — Vertex AI Agent Engine

Install the GCP SDK and deploy with one command:

```bash
pip install "google-cloud-aiplatform[preview]>=1.87.0" google-adk>=2.10.0

python examples/gemini/agent-engine/deploy.py \
    --project   my-gcp-project \
    --location  us-central1 \
    --store-name DreamHaus \
    --google-api-key AIza... \
    --sfcc-instance-url https://zzrl-008.dx.commercecloud.salesforce.com \
    --sfcc-client-id    CLIENT_ID \
    --sfcc-client-secret CLIENT_SECRET \
    --sfcc-site-id      DreamHaus
```

On success the script prints the `resource_name`, e.g.:

```
Deployed: projects/123456/locations/us-central1/reasoningEngines/789
```

#### Query the deployed engine

```python
import vertexai
from vertexai.preview import reasoning_engines

vertexai.init(project="my-gcp-project", location="us-central1")

engine = reasoning_engines.ReasoningEngine(
    "projects/123456/locations/us-central1/reasoningEngines/789"
)
response = engine.query(
    input="What are my top inventory alerts?",
    session_id="operator-session-1",
)
print(response["output"])
```

#### Delete a deployed engine

```bash
python examples/gemini/agent-engine/deploy.py \
    --delete \
    --resource-name projects/123456/locations/us-central1/reasoningEngines/789
```

## Environment variables

All variables from `examples/gemini/api/` apply here plus:

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Google AI Studio key (or pass `--google-api-key`) |
| `SFCC_INSTANCE_URL` | For live data | SFCC instance URL |
| `SFCC_CLIENT_ID` | For live data | OCAPI client ID |
| `SFCC_CLIENT_SECRET` | For live data | OCAPI client secret |
| `SFCC_SITE_ID` | For live data | Site ID |
| `SFCC_DISPLAY_SITE_ID` | No | Human-readable store name (default: DreamHaus) |
| `GEMINI_MODEL` | No | Model name (default: gemini-2.0-flash) |

GCP-specific (for deployment only):

| Variable | Description |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID (alternative to `--project`) |
| `GOOGLE_CLOUD_REGION` | GCP region (alternative to `--location`) |

## Files

| File | Purpose |
|---|---|
| `app.py` | `create_merchant_app()` — AdkApp wrapper with local fallback |
| `deploy.py` | CLI: deploy and delete on Vertex AI Agent Engine |
| `session_service.py` | SQLite-backed session service for self-hosted deployments |
