// SolutionsHub – Azure infrastructure (resource-group scope).
// Designed to deploy with *Contributor* on the resource group only: no role assignments, no Key Vault RBAC.
// Services authenticate to each other with keys/connection strings passed as App Service settings.
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

@description('Comma-separated list of email domains permitted to sign in.')
param allowedEmailDomains string = 'amentum.com,global.amentum.com,amentumcms.com,us.amentum.com,*.amentum.com'

@description('Email address that becomes the first Admin on first sign-in.')
param bootstrapAdminEmail string

@description('PostgreSQL administrator login name.')
param postgresAdminLogin string = 'solhubadmin'

@secure()
@description('PostgreSQL administrator password. Generated on first deployment if not supplied. Pass the existing value on re-deployments.')
param postgresAdminPassword string = '${uniqueString(newGuid())}${uniqueString(newGuid())}Aa1!'

@secure()
@description('Application secret used to sign session cookies. Generated on first deployment if not supplied. Pass the existing value on re-deployments (changing it signs everyone out).')
param appSecretKey string = '${newGuid()}${newGuid()}'

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
    allowSharedKeyAccess: true // the app authenticates with the account key (no RBAC available)
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

var storageKey = storage.listKeys().keys[0].value
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storageKey};EndpointSuffix=${az.environment().suffixes.storage}'

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
// outbound IPs as a hardening step.
resource postgresAzureRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

var databaseUrl = 'postgresql+psycopg://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/solutionshub?sslmode=require'

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

// --------------------------------------------------------------------------- app service
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'asp-${suffix}'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic', capacity: 1 }
  properties: { reserved: true }
}

// Publicly reachable by design: the only anonymous surface is the sign-in page, and sign-in is limited to
// the allowed email domains (see docs/04-auth-and-access.md).
resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: 'app-${suffix}-${uniq}'
  location: location
  tags: tags
  kind: 'app,linux'
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
      appSettings: [
        { name: 'APP_ENV', value: environment }
        { name: 'BASE_URL', value: 'https://app-${suffix}-${uniq}.azurewebsites.net' }
        { name: 'ALLOWED_EMAIL_DOMAINS', value: allowedEmailDomains }
        { name: 'BOOTSTRAP_ADMIN_EMAIL', value: bootstrapAdminEmail }
        { name: 'SECRET_KEY', value: appSecretKey }
        { name: 'DATABASE_URL', value: databaseUrl }
        { name: 'EMAIL_BACKEND', value: 'acs' }
        { name: 'ACS_CONNECTION_STRING', value: acs.listKeys().primaryConnectionString }
        { name: 'ACS_SENDER', value: 'DoNotReply@${emailDomain.properties.mailFromSenderDomain}' }
        { name: 'STORAGE_BACKEND', value: 'azure' }
        { name: 'AZURE_STORAGE_CONNECTION_STRING', value: storageConnectionString }
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

// --------------------------------------------------------------------------- outputs
output webAppName string = web.name
output webAppUrl string = 'https://${web.properties.defaultHostName}'
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output storageAccountName string = storage.name
output emailSender string = 'DoNotReply@${emailDomain.properties.mailFromSenderDomain}'
output appInsightsName string = appInsights.name
