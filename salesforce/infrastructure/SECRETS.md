# Secrets & Variables Reference

Configure these in GitHub → Settings → Secrets and variables → Actions.

## Repository variables (`vars.*`) — not secret, visible in logs

| Variable | Example | Description |
|---|---|---|
| `CLOUD_PROVIDER` | `aws` \| `azure` \| `both` | Which cloud backend to deploy to |
| `AWS_REGION` | `us-east-1` | AWS region for ECS + ECR + CloudFront |
| `ECR_REGISTRY` | `123456789.dkr.ecr.us-east-1.amazonaws.com` | ECR registry host |
| `ECS_CLUSTER` | `commerce-agents` | ECS cluster name |
| `SANDBOX_ECS_SERVICE` | `commerce-api-sandbox` | ECS service name for sandbox |
| `PROD_ECS_SERVICE` | `commerce-api-prod` | ECS service name for production |
| `SANDBOX_TASK_DEF_FAMILY` | `commerce-agent-api-sandbox` | Task definition family |
| `PROD_TASK_DEF_FAMILY` | `commerce-agent-api-prod` | Task definition family |
| `SANDBOX_S3_BUCKET` | `commerce-agents-sandbox` | S3 bucket for sandbox web apps |
| `PROD_S3_BUCKET` | `commerce-agents-prod` | S3 bucket for production web apps |
| `SANDBOX_CF_DIST_ID` | `EXXXXXXXXXXXXX` | CloudFront distribution for sandbox |
| `PROD_CF_DIST_ID` | `EYYYYYYYYYYYYY` | CloudFront distribution for production |
| `SANDBOX_API_URL` | `https://api.sandbox.example.com` | Deployed API base URL (sandbox) |
| `PROD_API_URL` | `https://api.example.com` | Deployed API base URL (production) |
| `ACR_LOGIN_SERVER` | `myacr.azurecr.io` | Azure Container Registry host |
| `ACR_NAME` | `myacr` | ACR name (without .azurecr.io) |
| `ACA_RESOURCE_GROUP` | `commerce-agents-rg` | Azure resource group |
| `SANDBOX_ACA_APP` | `commerce-api-sandbox` | Azure Container App name (sandbox) |
| `PROD_ACA_APP` | `commerce-api-prod` | Azure Container App name (production) |
| `SANDBOX_ACA_ENV` | `commerce-agents-env-sandbox` | ACA environment name |
| `PROD_ACA_ENV` | `commerce-agents-env-prod` | ACA environment name |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | Anthropic or LiteLLM proxy URL |

## Repository secrets (`secrets.*`) — encrypted, never shown in logs

### Salesforce (all environments)
| Secret | How to obtain |
|---|---|
| `SFDX_AUTH_URL_DEVHUB` | `sf org display --verbose --target-org <devhub>` → Auth URL field |
| `PRIVATE_KEY_BASE64` | `base64 -i server.key` — the private key for JWT auth |
| `SF_CLIENT_ID` | Connected App → Consumer Key (sandbox) |
| `SF_SANDBOX_USERNAME` | API user username in sandbox org |
| `SF_INSTANCE_URL` | `https://<org>.sandbox.my.salesforce.com` |
| `SF_PROD_CLIENT_ID` | Connected App → Consumer Key (production) |
| `SF_PROD_USERNAME` | API user username in production org |

### AI
| Secret | How to obtain |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |

### AWS
| Secret | How to obtain |
|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::<ACCOUNT>:role/GitHubActionsCommerceAgents` — see `aws/iam-oidc-policy.json` |

### Azure
| Secret | How to obtain |
|---|---|
| `AZURE_CLIENT_ID` | Service principal app (client) ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_SWA_TOKEN_STOREFRONT` | Azure Static Web Apps deployment token (storefront) |
| `AZURE_SWA_TOKEN_MERCHANT` | Azure Static Web Apps deployment token (merchant) |

## GitHub environments

Create two environments at Settings → Environments:

### `sandbox`
- No required reviewers
- Add all `SANDBOX_*` and `SF_*` (sandbox) variables/secrets here

### `production`
- Required reviewers: at least 2 named approvers
- Protection rule: only allow deployments from `main` branch
- Wait timer: 5 minutes (optional cool-off before deploy)
- Add all `PROD_*` and `SF_PROD_*` variables/secrets here

## One-time AWS setup

```bash
# 1. Add GitHub OIDC provider to your AWS account (once per account)
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
  --client-id-list sts.amazonaws.com

# 2. Create the IAM role
aws iam create-role \
  --role-name GitHubActionsCommerceAgents \
  --assume-role-policy-document file://aws/iam-oidc-policy.json

# 3. Attach permissions (scope these down for production)
aws iam attach-role-policy --role-name GitHubActionsCommerceAgents \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser
aws iam attach-role-policy --role-name GitHubActionsCommerceAgents \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess

# 4. Create ECR repository
aws ecr create-repository --repository-name commerce-agent-api

# 5. Create ECS cluster
aws ecs create-cluster --cluster-name commerce-agents

# 6. Register task definitions (fill in aws/ecs-task-definition.json first)
aws ecs register-task-definition \
  --cli-input-json file://aws/ecs-task-definition.json
```

## One-time Azure setup

```bash
# 1. Create service principal with federated credentials (OIDC — no password)
az ad app create --display-name "GitHubActionsCommerceAgents"
APP_ID=$(az ad app list --display-name "GitHubActionsCommerceAgents" --query "[0].appId" -o tsv)

az ad sp create --id $APP_ID
SP_OID=$(az ad sp show --id $APP_ID --query id -o tsv)

# 2. Add federated identity credentials for OIDC
az ad app federated-credential create --id $APP_ID --parameters '{
  "name": "github-actions",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<GITHUB_ORG>/<GITHUB_REPO>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

# 3. Create resource group and assign role
az group create --name commerce-agents-rg --location eastus
az role assignment create --role Contributor --assignee $SP_OID \
  --scope /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/commerce-agents-rg

# 4. Create ACR
az acr create --name <ACR_NAME> --resource-group commerce-agents-rg --sku Basic

# 5. Deploy Container Apps infrastructure
az deployment group create \
  --resource-group commerce-agents-rg \
  --template-file azure/container-app.bicep \
  --parameters environment=sandbox acrLoginServer=<ACR_NAME>.azurecr.io \
               sfInstanceUrl=<SF_URL> anthropicApiKey=<KEY>
```
