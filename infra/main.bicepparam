using './main.bicep'

param baseName = readEnvironmentVariable('SOLUTIONHUB_BASE_NAME', 'solhubdr')
param environment = readEnvironmentVariable('SOLUTIONHUB_ENVIRONMENT', 'dev')

param allowedEmailDomains = 'amentum.com,global.amentum.com,amentumcms.com,us.amentum.com,*.amentum.com'
param bootstrapAdminEmail = readEnvironmentVariable('BOOTSTRAP_ADMIN_EMAIL', 'darren.rourke@amentumcms.com')

// postgresAdminPassword and appSecretKey are generated on the first deployment.
// On later deployments pass the existing values so they are not regenerated, e.g.
//   az deployment group create ... -p postgresAdminPassword="$PG_PW" -p appSecretKey="$SECRET"
