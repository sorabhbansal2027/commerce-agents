// Azure Container Apps deployment for commerce-agent-api.
// Deploy with:
//   az deployment group create \
//     --resource-group commerce-agents-rg \
//     --template-file container-app.bicep \
//     --parameters environment=sandbox acrLoginServer=<ACR>.azurecr.io imageTag=latest

@description('Deployment environment: sandbox or production')
@allowed(['sandbox', 'production'])
param environment string

@description('ACR login server, e.g. myacr.azurecr.io')
param acrLoginServer string

@description('Image tag to deploy')
param imageTag string = 'latest'

@description('Azure region')
param location string = resourceGroup().location

@description('Salesforce instance URL')
@secure()
param sfInstanceUrl string

@description('Anthropic API key or LiteLLM proxy key')
@secure()
param anthropicApiKey string

// ── Container App Environment ─────────────────────────────────────────────────
resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'commerce-agents-env-${environment}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
  tags: {
    project: 'commerce-agents'
    environment: environment
    managedBy: 'bicep'
  }
}

// ── Container App ─────────────────────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'commerce-agent-api-${environment}'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: acaEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        allowInsecure: false
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
        corsPolicy: {
          allowedOrigins: environment == 'production' ? ['https://your-storefront.azurestaticapps.net'] : ['*']
          allowedMethods: ['GET', 'POST', 'OPTIONS']
          allowedHeaders: ['*']
        }
      }
      registries: [
        {
          server: acrLoginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'sf-instance-url'
          value: sfInstanceUrl
        }
        {
          name: 'anthropic-api-key'
          value: anthropicApiKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'commerce-agent-api'
          image: '${acrLoginServer}/commerce-agent-api:${imageTag}'
          resources: {
            cpu: json(environment == 'production' ? '1.0' : '0.5')
            memory: environment == 'production' ? '2Gi' : '1Gi'
          }
          env: [
            {
              name: 'PORT'
              value: '8000'
            }
            {
              name: 'ENVIRONMENT'
              value: environment
            }
            {
              name: 'SF_INSTANCE_URL'
              secretRef: 'sf-instance-url'
            }
            {
              name: 'ANTHROPIC_API_KEY'
              secretRef: 'anthropic-api-key'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: environment == 'production' ? 2 : 1
        maxReplicas: environment == 'production' ? 10 : 3
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
  tags: {
    project: 'commerce-agents'
    environment: environment
    managedBy: 'bicep'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output containerAppId string = containerApp.id
output principalId string = containerApp.identity.principalId
