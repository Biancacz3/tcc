param([switch]$CriarConta, [string]$ModeloOrigem = '')
$ErrorActionPreference = 'Stop'
$composeBase = @('compose', '--project-directory', $PSScriptRoot, '--env-file', (Join-Path $PSScriptRoot '.env'), '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-f', (Join-Path $PSScriptRoot 'compose.ia.yaml'))
function Invoke-Compose {
    param([string[]]$Comando)
    & docker @composeBase @Comando
    if ($LASTEXITCODE -ne 0) { throw ('Falha no Docker Compose: ' + ($Comando -join ' ')) }
}
function Read-Compose {
    param([string[]]$Comando)
    $resultado = & docker @composeBase @Comando
    if ($LASTEXITCODE -ne 0) { throw ('Falha no Docker Compose: ' + ($Comando -join ' ')) }
    return $resultado
}
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker nao encontrado. Instale e abra Docker Desktop.' }
& docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Abra Docker Desktop e tente novamente.' }
& (Join-Path $PSScriptRoot 'configurar.ps1') -ModeloOrigem $ModeloOrigem
Invoke-Compose -Comando @('config', '--quiet')
# Construcao e abertura do ONNX acontecem antes de interromper a aplicacao.
Invoke-Compose -Comando @('build', 'api', 'worker')
$modeloCodigo = "from app.inference import get_detector; d=get_detector(); print('MODELO_OK ' + d.sha256)"
Invoke-Compose -Comando @('run', '--rm', '-T', '--no-deps', 'api', 'python', '-c', $modeloCodigo)
Invoke-Compose -Comando @('up', '-d', '--wait', '--wait-timeout', '120', 'db')
Invoke-Compose -Comando @('stop', 'api', 'worker')
$usuarioDB = ([string](Read-Compose -Comando @('exec', '-T', 'db', 'printenv', 'POSTGRES_USER'))).Trim()
$nomeDB = ([string](Read-Compose -Comando @('exec', '-T', 'db', 'printenv', 'POSTGRES_DB'))).Trim()
if (!$usuarioDB -or !$nomeDB) { throw 'Nao foi possivel identificar o banco existente. API e worker permanecem parados.' }
$sufixo = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
$nomeBackup = 'banco-antes-interface-ia-' + $sufixo + '.dump'
$arquivoDB = Join-Path $PSScriptRoot ('backups\' + $nomeBackup)
$arquivoTemporario = '/tmp/' + $nomeBackup
Invoke-Compose -Comando @('exec', '-T', 'db', 'pg_dump', '-U', $usuarioDB, '-d', $nomeDB, '-Fc', '-f', $arquivoTemporario)
Read-Compose -Comando @('exec', '-T', 'db', 'pg_restore', '--list', $arquivoTemporario) | Out-Null
# Compose cp preserva os bytes; nao usar redirecionamento PowerShell para o dump.
Invoke-Compose -Comando @('cp', ('db:' + $arquivoTemporario), $arquivoDB)
if (!(Test-Path -LiteralPath $arquivoDB) -or (Get-Item -LiteralPath $arquivoDB).Length -eq 0) { throw 'Backup do banco nao foi confirmado.' }
Invoke-Compose -Comando @('exec', '-T', 'db', 'rm', '--', $arquivoTemporario)
Write-Host "Banco guardado em $arquivoDB"
try {
    Invoke-Compose -Comando @('run', '--rm', '-T', '--no-deps', 'api', 'alembic', 'upgrade', 'head')
    Invoke-Compose -Comando @('run', '--rm', '-T', '--no-deps', 'api', 'alembic', 'check')
} catch {
    Write-Host 'A migracao ou verificacao do esquema falhou. API e worker permanecem parados.'
    Write-Host ('Backup do banco preservado: ' + $arquivoDB)
    throw
}
$contarCodigo = "from sqlalchemy import func, select; from app.db.session import SessionLocal; from app.models.user import User; s=SessionLocal(); n=s.scalar(select(func.count()).select_from(User).where(User.role=='admin', User.is_active.is_(True))); s.close(); print('TCC_ACTIVE_ADMIN=' + str(n))"
$contagem = @(Read-Compose -Comando @('run', '--rm', '-T', '--no-deps', 'api', 'python', '-c', $contarCodigo) | Where-Object { $_ -match '^TCC_ACTIVE_ADMIN=\d+$' })
if ($contagem.Count -ne 1) { throw 'Nao foi possivel verificar a conta administrativa. Nenhuma conta foi alterada.' }
$administradores = [int](($contagem[0] -split '=', 2)[1])
if ($CriarConta -or $administradores -eq 0) {
    Write-Host 'Cadastre a conta administrativa para entrar no painel (senha com 12 a 128 caracteres).'
    Invoke-Compose -Comando @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'app.manage_users', 'create')
} else {
    Write-Host 'Conta administrativa existente preservada. Entre com seu e-mail e senha atuais.'
}
& (Join-Path $PSScriptRoot 'iniciar.ps1')
