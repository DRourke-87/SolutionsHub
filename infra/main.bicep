// SolutionsHub – Azure infrastructure (resource-group scope).
// Deploy:  az deployment group create -g <rg> -f infra/main.bicep -p infra/main.bicepparam
targetScope = 'resourceGroup'

@description('Short name used as a prefix for all resources (lowercase letters and digits).')
@minLength(3)
@maxLength(12)
param baseName string = 'solhub'

@description('Environment label appended to resource names.')
@allowed(['dev', 'test', 'prod'])
param environment string = 'prod'

param location string = resourceGroup().location

@description('VPN egress IP ranges (CIDR) allowed to reach the web app. Everything else is denied.')
param allowedIpRanges array

@description('Comma-separated list of email domains permitted to sign in.')
param allowedEmailDomains string = 'amentum.com,global.amentum.com,amentumcms.com'

@description('Email address that becomes the first Admin on first sign-in.')
param bootstrapAdminEmail string

@description('PostgreSQL administrator login name.')
param postgresAdminLogin string = 'solhubadmin'

@secure()
@description('PostgreSQL administrator password. Generated if not supplied; stored in Key Vault.')
param postgresAdminPassword string = '${uniqueString(newGuid())}${uniqueString(newGuid())}Aa1!'

@secure()
@description('Application secret used to sign session cookies. Generated if not supplied; stored in Key Vault.')
param appSecretKey string = '${newGuid()}${newGuid()}'

@description('Object id of the operator (user or group) who should manage Key Vault secrets. Optional.')
param keyVaultAdminObjectId string = ''

@description('Application Insights / Log Analytics daily ingestion cap in GB (keeps the workload inside the free grant).')
param logDailyCapGb int = 1

var suffix = toLower('${baseName}-${environment}')
var uniq = toLower(substring(uniqueString(resourceGroup().id, baseName, environment), 0, 6))
var tags = { workload: 'SolutionsHub', environment: environment, managedBy: 'bicep' }

// --------------------------------------------------------------------------- monitoring
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: logDailyCapGb }
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// --------------------------------------------------------------------------- storage (attachments)
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${replace(suffix, '-', '')}${uniq}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 30 }
    containerDeleteRetentionPolicy: { enabled: true, days: 30 }
    isVersioningEnabled: true
  }
}

resource attachmentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'attachments'
  properties: { publicAccess: 'None' }
}

// --------------------------------------------------------------------------- key vault
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${suffix}-${uniq}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

// --------------------------------------------------------------------------- postgresql
resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: 'psql-${suffix}-${uniq}'
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    authConfig: { passwordAuth: 'Enabled', activeDirectoryAuth: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource postgresDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: 'solutionshub'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// Allows connections from Azure services (the App Service outbound IPs). Tighten to the web app's
// outbound IPs or move to VNet integration as a hardening step.
resource postgresAzureRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// --------------------------------------------------------------------------- communication services (email)
resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: 'email-${suffix}'
  location: 'global'
  tags: tags
  properties: { dataLocation: 'United States' }
}

// Azure-managed sender domain: zero DNS work. Replace with a verified custom domain before go-live.
resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  properties: { domainManagement: 'AzureManaged', userEngagementTracking: 'Disabled' }
}

resource acs 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: 'acs-${suffix}-${uniq}'
  location: 'global'
  tags: tags
  properties: { dataLocation: 'United States', linkedDomains: [emailDomain.id] }
}

// --------------------------------------------------------------------------- secrets
resource secretDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'DATABASE-URL'
  properties: {
    value: 'postgresql+psycopg://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/solutionshub?sslmode=require'
  }
}

resource secretAppKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'SECRET-KEY'
  properties: { value: appSecretKey }
}

resource secretAcs 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'ACS-CONNECTION-STRING'
  properties: { value: acs.listKeys().primaryConnectionString }
}

// --------------------------------------------------------------------------- app service
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'asp-${suffix}'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic', capacity: 1 }
  properties: { reserved: true }
}

var ipRules = [for (cidr, i) in allowedIpRanges: {
  name: 'vpn-${i}'
  ipAddress: cidr
  action: 'Allow'
  priority: 100 + i
  description: 'Corporate VPN egress'
}]

resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: 'app-${suffix}-${uniq}'
  location: location
  tags: tags
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'bash startup.sh'
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/health'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: ipRules
      scmIpSecurityRestrictionsUseMain: true
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
      appSettings: [
        { name: 'APP_ENV', value: environment == 'prod' ? 'prod' : environment }
        { name: 'BASE_URL', value: 'https://app-${suffix}-${uniq}.azurewebsites.net' }
        { name: 'ALLOWED_EMAIL_DOMAINS', value: allowedEmailDomains }
        { name: 'BOOTSTRAP_ADMIN_EMAIL', value: bootstrapAdminEmail }
        { name: 'SECRET_KEY', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=SECRET-KEY)' }
        { name: 'DATABASE_URL', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=DATABASE-URL)' }
        { name: 'EMAIL_BACKEND', value: 'acs' }
        { name: 'ACS_CONNECTION_STRING', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=ACS-CONNECTION-STRING)' }
        { name: 'ACS_SENDER', value: 'DoNotReply@${emailDomain.properties.mailFromSenderDomain}' }
        { name: 'STORAGE_BACKEND', value: 'azure' }
        { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
        { name: 'AZURE_STORAGE_CONTAINER', value: 'attachments' }
        { name: 'SCHEDULER_ENABLED', value: 'true' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '600' }
        { name: 'PYTHONUNBUFFERED', value: '1' }
      ]
    }
  }
}

// --------------------------------------------------------------------------- RBAC for the web app's managed identity
var roleKeyVaultSecretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var roleStorageBlobDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var roleKeyVaultSecretsOfficer = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')

resource kvReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, web.id, roleKeyVaultSecretsUser)
  scope: kv
  properties: { roleDefinitionId: roleKeyVaultSecretsUser, principalId: web.identity.principalId, principalType: 'ServicePrincipal' }
}

resource blobWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, web.id, roleStorageBlobDataContributor)
  scope: storage
  properties: { roleDefinitionId: roleStorageBlobDataContributor, principalId: web.identity.principalId, principalType: 'ServicePrincipal' }
}

resource kvOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultAdminObjectId)) {
  name: guid(kv.id, keyVaultAdminObjectId, roleKeyVaultSecretsOfficer)
  scope: kv
  properties: { roleDefinitionId: roleKeyVaultSecretsOfficer, principalId: keyVaultAdminObjectId }
}

// --------------------------------------------------------------------------- outputs
output webAppName string = web.name
output webAppUrl string = 'https://${web.properties.defaultHostName}'
output webAppPrincipalId string = web.identity.principalId
output keyVaultName string = kv.name
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output storageAccountName string = storage.name
output emailSender string = 'DoNotReply@${emailDomain.properties.mailFromSenderDomain}'
output appInsightsName string = appInsights.name
