#!/usr/bin/env bash
# One-time setup for GCP CI/CD via Cloud Build GitHub triggers.
#
# Run this once, then connect the repo in GCP Console:
#   Cloud Build → Triggers → Connect repository (GitHub App)
#   Create trigger:
#     Name:           deploy-gemini-on-push
#     Event:          Push to branch
#     Branch:         ^main$
#     Config file:    examples/gemini/cloudbuild.yaml
#     Service account: (default Cloud Build SA — already granted below)
#
# Usage:
#   export GOOGLE_API_KEY=your-gemini-key
#   export GOOGLE_KG_API_KEY=your-kg-key
#   ./scripts/setup_gcp_cicd.sh

set -euo pipefail

GCP_PROJECT="spartan-concord-510108-p2"
GCP_REGION="us-central1"

echo "▶ Enabling required GCP APIs..."
gcloud services enable \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="${GCP_PROJECT}" --quiet

# ── Cloud Build SA permissions ─────────────────────────────────────────────────
CB_SA="$(gcloud projects describe "${GCP_PROJECT}" \
  --format='value(projectNumber)')@cloudbuild.gserviceaccount.com"

echo "▶ Granting Cloud Build SA roles..."
for role in roles/run.admin roles/iam.serviceAccountUser roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "${GCP_PROJECT}" \
    --member="serviceAccount:${CB_SA}" \
    --role="${role}" \
    --condition=None --quiet 2>/dev/null || true
done

# ── Store API keys in Secret Manager ──────────────────────────────────────────
echo "▶ Storing secrets in Secret Manager..."

store_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "${name}" --project="${GCP_PROJECT}" &>/dev/null; then
    echo "   ${name}: adding new version"
    echo -n "${value}" | gcloud secrets versions add "${name}" \
      --project="${GCP_PROJECT}" --data-file=-
  else
    echo "   ${name}: creating"
    echo -n "${value}" | gcloud secrets create "${name}" \
      --project="${GCP_PROJECT}" \
      --replication-policy=automatic \
      --data-file=-
  fi
  # Per-secret IAM (belt-and-suspenders alongside the project-level binding above)
  gcloud secrets add-iam-policy-binding "${name}" \
    --project="${GCP_PROJECT}" \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/secretmanager.secretAccessor" --quiet 2>/dev/null || true
}

store_secret "GOOGLE_API_KEY"    "${GOOGLE_API_KEY:?GOOGLE_API_KEY env var required}"
store_secret "GOOGLE_KG_API_KEY" "${GOOGLE_KG_API_KEY:?GOOGLE_KG_API_KEY env var required}"

echo ""
echo "✓ Setup complete."
echo ""
echo "Next: connect the repo and create the trigger in GCP Console:"
echo "  https://console.cloud.google.com/cloud-build/triggers?project=${GCP_PROJECT}"
echo ""
echo "Trigger settings:"
echo "  Name:        deploy-gemini-on-push"
echo "  Event:       Push to branch"
echo "  Branch:      ^main$"
echo "  Config file: examples/gemini/cloudbuild.yaml"
