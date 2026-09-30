# Recebimento de capturas

Etapa de 30/09/2026. A camera da Raspberry Pi 3B ja foi testada. Esta etapa recebe
e armazena fotos; ainda nao realiza inferencia de IA nem instala o agente automatico.

## Contrato da API

POST `/api/v1/captures`, com o JPEG diretamente no corpo (nao JSON ou multipart):

- `Authorization: Bearer CHAVE_DO_DISPOSITIVO`
- `Content-Type: image/jpeg`
- `X-Capture-Id`: UUID criado uma unica vez por captura.
- `X-Captured-At`: instante da fotografia em ISO 8601 com fuso, por exemplo
  `2026-09-30T14:00:00-03:00`.

O agente deve guardar o UUID, o instante e os bytes originais na fila e reutilizar
esses mesmos valores em todas as tentativas. Um novo UUID representa outra captura.

Respostas: 201 para nova captura; 200 para reenvio identico; 409 para mesmo UUID com
outro dispositivo, outra imagem ou outro instante. 401 indica credencial ausente,
invalida ou dispositivo inativo; 413 excede o limite; 415 tipo de conteudo incorreto;
422 JPEG ou metadados invalidos; 503 falha de persistencia, permitindo tentar depois
com o mesmo UUID. A chave administrativa nao autentica o envio de uma camera.

Limites: 10 MiB de arquivo e 20 megapixels. Pillow decodifica o JPEG para validar
seu conteudo; o servidor verifica o tamanho durante a leitura do corpo, mesmo sem
Content-Length. O corpo e limitado por requisicao; esta versao e um prototipo local,
sem controles globais de concorrencia ou quota de armazenamento.

GET `/api/v1/captures?limit=50&offset=0` lista metadados, do recebimento mais recente
para o mais antigo. O limite maximo e 100. GET `/api/v1/captures/{id}/image` devolve
o JPEG original. Ambos exigem chave administrativa. Abrir image_url diretamente no
navegador sem o cabecalho Authorization retorna 401.

`status=received` significa que a imagem foi recebida, nao que foi analisada por IA.

## Persistencia e recuperacao

JPEGs ficam em `storage/UUID_DISPOSITIVO/UUID_CAPTURA.jpg`, no Windows. O Compose
monta essa pasta em `/app/storage`. PostgreSQL guarda metadados, hash SHA-256 e a
referencia ao arquivo. `storage/` deve continuar ignorada pelo Git.

A chave primaria de captures impede registros duplicados, inclusive em duas
requisicoes simultaneas. O arquivo e escrito em um temporario no mesmo diretorio,
sincronizado e movido para o nome final; depois o registro e confirmado no banco.
Falhas retornam 503, sem confirmar o recebimento ao dispositivo. Um reenvio identico
tambem restaura o arquivo caso o registro exista mas a imagem tenha sumido do disco.

Banco e arquivos nao compartilham uma transacao atomica. Uma interrupcao entre a
gravacao do JPEG e o commit pode deixar um arquivo sem registro. A repeticao com o
mesmo UUID recupera esse caso. Nao ha limpeza automatica desses arquivos nesta etapa.
Em alguns bind mounts do Docker Desktop o sistema nao suporta fsync de diretorio;
nesse caso e emitido um aviso no log. Isso limita a garantia contra queda abrupta de
energia, sem impedir a persistencia normal do arquivo.

Para continuar em outra maquina, e necessario transferir tambem `storage/`, um
backup PostgreSQL e os arquivos de configuracao privados `.env` e `.env.raspberry`.
Git sincroniza somente o codigo. Nao use `docker compose down -v` para atualizar.

No Windows/Docker Desktop, crie storage antes de subir a API. Em um host Linux,
o usuario do container (UID 10001, conforme Dockerfile atual) precisa de permissao
de escrita na pasta montada. Prepare o proprietario/permissoes para esse UID.

## Instalacao no projeto existente

O ZIP e uma atualizacao parcial, nao um projeto completo. Requer as migracoes
0001_devices e 0002_device_tokens e os endpoints de credenciais individuais.
Nao substitua essas migracoes antigas pela nova 0003_captures.

Salve tcc-capturas.zip em C:\TCC. No PowerShell do Windows, fora da sessao SSH:

```powershell
cd C:\TCC\tcc
$backupCapturas = "C:\TCC\backup-capturas-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Path $backupCapturas | Out-Null
Copy-Item .\backend "$backupCapturas\backend" -Recurse
Copy-Item .\compose.yaml "$backupCapturas\compose.yaml"
Expand-Archive -LiteralPath C:\TCC\tcc-capturas.zip -DestinationPath . -Force
New-Item -ItemType Directory -Force .\storage | Out-Null
docker compose up --build -d --wait
docker compose exec api alembic upgrade head
docker compose exec api alembic current
docker compose exec api alembic check
```

Esperado: `0003_captures (head)` e `No new upgrade operations detected`.
Se qualquer comando falhar, pare e consulte `docker compose logs --tail=80 api`.
A nova migracao e aditiva e nao apaga os dispositivos existentes.

## Teste real com a fotografia

O teste abaixo EMITE UMA NOVA CHAVE para o dispositivo escolhido, invalidando sua
chave anterior. Nao altera a senha SSH ou a chave administrativa. A nova chave e
guardada em /tmp/raspberry.env dentro do container e nao e impressa. Copie esse
arquivo para o Windows logo apos o teste; /tmp nao e persistente ao recriar o container.

O UUID abaixo corresponde a Raspberry 01 que foi cadastrada nesta conversa. Se ela
foi excluida, consulte a listagem administrativa e use o UUID correto.

```powershell
docker compose cp C:\TCC\teste-raspberry.jpg api:/tmp/teste.jpg
docker compose cp .\tools\testar_captura.py api:/tmp/testar_captura.py
docker compose exec api python /tmp/testar_captura.py /tmp/teste.jpg 7c57af93-e768-42ba-9a9f-c50b1c506e88
docker compose cp api:/tmp/raspberry.env .env.raspberry
git check-ignore .env.raspberry storage/
```

O teste verifica autenticacao, arquivo falso, data sem fuso, arquivo grande, primeiro
envio 201, repeticao 200, conflito 409, listagem e download autenticados. Confere
tambem que os bytes baixados sao iguais aos enviados e que o ID so aparece uma vez.
O horario dessa captura de teste e o horario de modificacao do arquivo copiado.
O futuro agente enviara o horario real registrado no momento da fotografia.

Esperado: `TODOS OS TESTES PASSARAM.`. Se um teste falhar depois de emitir a chave,
ainda copie /tmp/raspberry.env antes de recriar o container para preserva-la.
Cada execucao bem-sucedida cria uma nova captura de teste e gira a credencial.

O teste roda pelo endereco local da API. A porta continua publicada em 127.0.0.1;
o acesso direto da Raspberry a API e a configuracao do firewall serao feitos na
proxima etapa, junto do agente de captura. SSH e SCP ja funcionam independentemente.

Conferencia opcional no banco:

```powershell
docker compose exec db psql -U tcc_admin -d tcc_pragas -c "SELECT id, device_id, status, width, height, size_bytes FROM captures ORDER BY received_at DESC LIMIT 5;"
```

## Documentacao e Git

Depois dos testes, acrescente ao readme.md existente uma referencia a este documento.
O comando abaixo adiciona o link somente se ainda nao existir:

```powershell
if (-not (Select-String -Path .\readme.md -Pattern 'docs/capturas.md' -SimpleMatch -Quiet)) {
    Add-Content -Path .\readme.md -Value "`nCaptura de imagens: [configuracao e testes](docs/capturas.md)."
}
git add backend/app/main.py backend/app/models/__init__.py backend/app/models/capture.py backend/app/schemas/capture.py backend/app/api/captures.py backend/alembic/versions/0003_captures.py backend/requirements.txt compose.yaml tools/testar_captura.py docs/capturas.md readme.md
git diff --cached --stat
git commit -m "Adiciona recebimento autenticado e armazenamento de capturas"
git push
```

O comando git add nao inclui credenciais nem fotografias. Se houver alteracoes da
etapa anterior ainda sem commit, revise-as com git status e salve-as tambem para
que o projeto possa ser retomado em outra maquina com todas as dependencias.

## Verificacao feita antes da entrega

19 testes passaram com Python 3.12, FastAPI 0.141.1, SQLAlchemy 2.1.1 e Pillow 12.3.0.
Usaram TestClient, SQLite temporario e dependencias de apoio equivalentes para
autenticacao/sessoes; nao executaram seu ambiente completo. Cobriram captura,
repeticao, conflito por data/imagem/dispositivo, fuso equivalente, reparo do arquivo,
credenciais invalidas/inativas, JPEG truncado, limites, erros de disco/commit e
contrato OpenAPI. O teste local ajusta o fuso perdido pelo SQLite para simular a
leitura TIMESTAMPTZ do PostgreSQL. A migracao tambem foi compilada para PostgreSQL.

Nao houve execucao local de PostgreSQL, Docker Desktop, Python 3.13 ou teste de
concorrencia real entre processos. Os comandos acima validam seu ambiente de destino.

Referencias oficiais usadas no desenvolvimento:
- https://starlette.dev/requests/
- https://pillow.readthedocs.io/en/stable/reference/Image.html
- https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html
