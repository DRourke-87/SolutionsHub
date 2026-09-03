using './main.bicep'

param baseName = 'solhub'
param environment = 'prod'

param allowedEmailDomains = 'amentum.com,global.amentum.com,amentumcms.com'
param bootstrapAdminEmail = 'first.admin@amentum.com'

// postgresAdminPassword and appSecretKey are generated on the first deployment.
// On later deployments pass the existing values so they are not regenerated, e.g.
//   az deployment group create ... -p postgresAdminPassword="$PG_PW" -p appSecretKey="$SECRET"
