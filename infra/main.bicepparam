using './main.bicep'

param baseName = 'solhub'
param environment = 'prod'

// Replace with the real VPN egress ranges for both corporate domains (CIDR).
param allowedIpRanges = [
  '203.0.113.10/32'
  '198.51.100.0/24'
]

param allowedEmailDomains = 'amentum.com,global.amentum.com,amentumcms.com'
param bootstrapAdminEmail = 'first.admin@amentum.com'

// Optional: object id of the operator or group that should manage Key Vault secrets.
param keyVaultAdminObjectId = ''
