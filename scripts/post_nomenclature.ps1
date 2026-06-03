param(
    [string]$Out = "",
    [string]$Login = $env:IIKO_API_LOGIN,
    [string]$Org = $env:IIKO_ORGANIZATION_ID,
    [string]$BaseUrl = $(if ($env:IIKO_BASE_URL) { $env:IIKO_BASE_URL } else { "https://api-ru.iiko.services" })
)

$ErrorActionPreference = "Stop"

if (-not $Login) {
    throw "Set IIKO_API_LOGIN or pass -Login."
}
if (-not $Org) {
    throw "Set IIKO_ORGANIZATION_ID or pass -Org."
}

$BaseUrl = $BaseUrl.TrimEnd("/")

$tokenResponse = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/1/access_token" `
    -ContentType "application/json" `
    -Body (@{ apiLogin = $Login } | ConvertTo-Json -Compress)

$token = $tokenResponse.token
if (-not $token) {
    throw "No token in iiko access_token response."
}

Write-Host "POST $BaseUrl/api/1/nomenclature (organizationId=$Org)"

$headers = @{ Authorization = "Bearer $token" }
$body = @{ organizationId = $Org } | ConvertTo-Json -Compress

$response = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/1/nomenclature" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 120

if ($Out) {
    $response | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $Out -Encoding UTF8
    Write-Host "Saved: $Out"
}

$groups = @($response.groups)
$products = @($response.products)
Write-Host ("groups(top-level count): {0}" -f $groups.Count)
Write-Host ("products(count): {0}" -f $products.Count)
