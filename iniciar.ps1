$ErrorActionPreference = 'Stop'
$arquivoEnv = Join-Path $PSScriptRoot '.env'
$composeBase = @('compose', '--project-directory', $PSScriptRoot, '--env-file', $arquivoEnv, '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-f', (Join-Path $PSScriptRoot 'compose.ia.yaml'))
function Invoke-Compose {
    param([string[]]$Comando)
    & docker @composeBase @Comando
    if ($LASTEXITCODE -ne 0) { throw ('Falha no Docker Compose: ' + ($Comando -join ' ')) }
}
if (!(Test-Path -LiteralPath $arquivoEnv -PathType Leaf)) { throw 'Configuracao ausente. Execute aplicar-atualizacao.ps1 do pacote.' }
$endereco = [Environment]::GetEnvironmentVariable('API_BIND_ADDRESS')
if (!$endereco) {
    $linha = Get-Content -LiteralPath $arquivoEnv | Where-Object { $_ -match '^\s*API_BIND_ADDRESS\s*=' } | Select-Object -Last 1
    if ($linha) { $endereco = ($linha -split '=', 2)[1].Trim().Trim('"').Trim("'") }
}
if (!$endereco -or $endereco -eq '0.0.0.0' -or $endereco -eq '::') { $endereco = '127.0.0.1' }
if ($endereco.Contains(':') -and !$endereco.StartsWith('[')) { $endereco = '[' + $endereco + ']' }
$saudeUrl = 'http://' + $endereco + ':8000/api/v1/health/db'
Invoke-Compose -Comando @('up', '-d', '--wait', '--wait-timeout', '120', 'db')
# Confere a revisao efetiva antes de iniciar API/worker; nao altera o banco.
$esquemaCodigo = "from alembic.config import Config; from alembic.script import ScriptDirectory; from alembic.runtime.migration import MigrationContext; from app.db.engine import engine; c=engine.connect(); atual=set(MigrationContext.configure(c).get_current_heads()); esperado=set(ScriptDirectory.from_config(Config('alembic.ini')).get_heads()); c.close(); assert atual==esperado, 'Esquema pendente: execute instalar.ps1 antes de iniciar'; print('ESQUEMA_OK')"
Invoke-Compose -Comando @('run', '--rm', '-T', '--no-deps', 'api', 'python', '-c', $esquemaCodigo)
Invoke-Compose -Comando @('up', '-d', '--force-recreate', 'api', 'worker')
$pronto = $false
for ($tentativa = 0; $tentativa -lt 30; $tentativa++) {
    try {
        $resposta = Invoke-RestMethod -Uri $saudeUrl -TimeoutSec 3
        if ($resposta.status -eq 'ok') { $pronto = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
Invoke-Compose -Comando @('ps')
if (!$pronto) { throw ('API ainda nao respondeu. Consulte os logs na pasta ' + $PSScriptRoot + ': docker compose -f compose.yaml -f compose.ia.yaml logs --tail 60 api worker') }
$workerIds = @(& docker @composeBase ps -q worker)
if ($LASTEXITCODE -ne 0 -or $workerIds.Count -ne 1) { throw 'O processo de analise nao esta ativo. Consulte os logs do worker.' }
$workerAtivo = (& docker inspect --format '{{.State.Running}}' $workerIds[0])
if ($LASTEXITCODE -ne 0 -or ([string]$workerAtivo).Trim() -ne 'true') { throw 'O worker parou. Consulte os logs do worker antes de enviar fotos.' }
Write-Host 'API, banco e processo de analise em execucao.'
Write-Host ('Painel: http://' + $endereco + ':8000/painel/')
Write-Host 'Entre com seu e-mail e senha. Envie uma foto pelo painel: a analise entra na fila automaticamente.'
