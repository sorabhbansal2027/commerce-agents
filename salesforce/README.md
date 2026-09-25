# Salesforce DX — Commerce Agent

Salesforce DX project containing the Apex classes, triggers, and LWC components
for the commerce agent integration. Part of the broader `commerce-agents-main` monorepo.

## Structure

```
salesforce/
├── force-app/main/default/
│   ├── classes/          Apex — controller, service, trigger handler, tests
│   ├── triggers/         One trigger per object → handler.run()
│   ├── lwc/              LWC components with Jest tests
│   ├── permissionsets/   Commerce_Agent_User permission set
│   └── namedCredentials/ Commerce_Agent_API (endpoint set per-org in Setup)
├── config/
│   └── project-scratch-def.json   Scratch org definition
├── jest-mocks/           Stubs for @salesforce/apex, schema, user
├── scripts/
│   ├── check_coverage.py  Apex coverage gate (75% threshold)
│   ├── post_ai_review.py  Claude PR review comment
│   └── explain_pmd_violations.py  Claude PMD explainer
├── docs/
│   └── ai-workflow.html   Interactive workflow diagram
├── sfdx-project.json
├── jest.config.js
└── package.json
```

## Apex patterns

| Pattern | Where |
|---------|-------|
| One trigger per object, all logic in handler | `triggers/`, `classes/OrderTriggerHandler.cls` |
| Handler extends `TriggerHandler` base | `classes/TriggerHandler.cls` |
| Service layer (bulkified, no SOQL in loops) | `classes/OrderService.cls` |
| `IHttpService` interface for testable callouts | `classes/IHttpService.cls` |
| `with sharing` on all public classes | All `.cls` files |
| `@AuraEnabled` controller fronts service | `classes/CommerceAgentController.cls` |
| Named Credential for external API | `namedCredentials/Commerce_Agent_API` |

## Setup

### Prerequisites

- [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli) (`sf` v2+)
- Node.js 22+
- Python 3.11+ (for scripts)

### One-time Dev Hub auth

```bash
sf org login web --alias my-devhub --set-default-dev-hub
```

### Create a scratch org

```bash
sf org create scratch \
  --definition-file config/project-scratch-def.json \
  --alias dev-scratch \
  --duration-days 30
```

### Deploy source

```bash
sf project deploy start \
  --source-dir force-app \
  --target-org dev-scratch
```

### Run Apex tests

```bash
sf apex run test \
  --target-org dev-scratch \
  --test-level RunLocalTests \
  --result-format human \
  --wait 10
```

### Run LWC Jest tests

```bash
npm install
npm run test:unit:coverage
```

### Named Credential setup

After deploying, update the `Commerce_Agent_API` Named Credential endpoint in
**Setup → Named Credentials** to point to your Railway (or local) FastAPI URL.

## Git workflow

| Branch | Maps to | CI | Approval |
|--------|---------|-----|----------|
| `feature/*` | scratch org | apex-scan + lwc-tests + scratch-org-validate | PR review |
| `develop` | sandbox | all stages + sandbox-deploy | 1 reviewer + CI |
| `main` | production | all stages + production-deploy | 2 reviewers + `production` environment gate |
| `hotfix/*` | scratch org | same as feature | Merge to both `main` and `develop` |

## AI-assisted development

### Claude Code (local)

```bash
# In the salesforce/ directory with Claude Code:
/review   # Reviews current Apex file for anti-patterns
/test     # Generates a complete @isTest class for the current Apex class
/explain  # Explains any SOQL or Apex pattern
```

### GitHub Copilot (VS Code)

Install the [Salesforce Extension Pack](https://marketplace.visualstudio.com/items?itemName=salesforce.salesforcedx-vscode)
alongside GitHub Copilot. The extension provides schema-aware Apex completions;
Copilot chat understands the `force-app/` layout and SFDX conventions.

### Claude in CI

- **On every PR**: Claude posts a structured Apex/LWC review comment with risk flags
- **On PMD failure**: Claude translates each violation code into plain English with a fix

See `docs/ai-workflow.html` for an interactive diagram of the full loop.

## Secrets required

| Secret | Environment | Description |
|--------|-------------|-------------|
| `SFDX_AUTH_URL_DEVHUB` | repo-level | sfdxurl from `sf org display --verbose` on Dev Hub |
| `PRIVATE_KEY_BASE64` | sandbox, production | Base64-encoded JWT private key |
| `SF_CLIENT_ID` | sandbox | Connected App consumer key |
| `SF_SANDBOX_USERNAME` | sandbox | API user for JWT auth |
| `SF_INSTANCE_URL` | sandbox | Sandbox instance URL |
| `SF_PROD_CLIENT_ID` | production | Production Connected App key |
| `SF_PROD_USERNAME` | production | Production API user |
| `ANTHROPIC_API_KEY` | repo-level | For AI PR review and PMD explainer |

### JWT key setup (one-time per environment)

```bash
openssl genrsa -out server.key 2048
openssl req -new -x509 -nodes -sha256 -days 365 -key server.key -out server.crt
# Upload server.crt to Connected App → Enable Digital Signature
# Store base64-encoded key as GitHub secret:
base64 -i server.key | pbcopy   # macOS — paste into PRIVATE_KEY_BASE64 secret
rm server.key server.crt        # never commit these
```
