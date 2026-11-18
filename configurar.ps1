param([string]$ModeloOrigem = '')
$ErrorActionPreference = 'Stop'
function New-SecretHex {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$arquivoEnv = Join-Path $PSScriptRoot '.env'
if (!(Test-Path -LiteralPath $arquivoEnv -PathType Leaf)) {
    $conteudo = @(
        'POSTGRES_DB=tcc_pragas'
        'POSTGRES_USER=tcc_admin'
        ('POSTGRES_PASSWORD=' + (New-SecretHex))
        ('ADMIN_API_TOKEN=' + (New-SecretHex))
        'API_BIND_ADDRESS=0.0.0.0'
        'SESSION_COOKIE_SECURE=false'
        'SESSION_DAYS=7'
        'ALLOW_LEGACY_ADMIN_TOKEN=false'
    ) -join "`n"
    [System.IO.File]::WriteAllText($arquivoEnv, $conteudo + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host '.env criado com segredos aleatorios; mantenha esse arquivo privado.'
} else {
    Write-Host '.env existente preservado.'
}
foreach ($pasta in @('storage', 'artifacts\models', 'backups')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot $pasta) | Out-Null
}
$destinoModelo = Join-Path $PSScriptRoot 'artifacts\models\pulgao-v1.onnx'
if (Test-Path -LiteralPath $destinoModelo -PathType Leaf) {
    Write-Host 'Modelo instalado preservado; o instalador verificara se ele pode ser carregado.'
} elseif ($ModeloOrigem) {
    if (!(Test-Path -LiteralPath $ModeloOrigem -PathType Leaf)) { throw 'ONNX de origem nao encontrado.' }
    $esperado = 'cde9133603b04fab3f0f6f1a244e19adbebb398ce4a3900e51f0c252a234fb60'
    if ((Get-FileHash -LiteralPath $ModeloOrigem -Algorithm SHA256).Hash.ToLowerInvariant() -ne $esperado) {
        throw 'O modelo do pacote nao passou na verificacao SHA256; nenhum modelo foi instalado.'
    }
    Copy-Item -LiteralPath $ModeloOrigem -Destination $destinoModelo
    Write-Host 'Modelo do pacote instalado e SHA256 confirmado.'
}
if (!(Test-Path -LiteralPath $destinoModelo -PathType Leaf)) {
    throw 'Falta artifacts\models\pulgao-v1.onnx. Execute aplicar-atualizacao.ps1 do pacote completo.'
}
Write-Host 'Configuracao local pronta. O modelo mantem as classes do treinamento original.'
