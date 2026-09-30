# Agente da Raspberry Pi 3B / Camera Module 3

Captura JPEG 2304x1296 com autofocus usando rpicam-still e envia para
POST /api/v1/captures. Usa Python 3.11 ou posterior e somente a biblioteca padrao.
O rpicam-still precisa estar instalado e a camera deve funcionar no teste manual.
O codigo deve ser executado na Raspberry (Linux), nao no Python do Windows.

## Preparacao do Windows

O ZIP atualiza raspberry/capture_and_send.py e inclui a unidade systemd. E uma
atualizacao do repositorio existente, nao um projeto novo. Antes de substituir,
preserve qualquer alteracao propria que tenha feito no script antigo.

Salve tcc-raspberry.zip em C:\TCC e execute no PowerShell do Windows:

```powershell
cd C:\TCC\tcc
Copy-Item .\raspberry\capture_and_send.py C:\TCC\capture_and_send-antes-do-agente.py
Expand-Archive -LiteralPath C:\TCC\tcc-raspberry.zip -DestinationPath . -Force
```

Edite C:\TCC\tcc\.env.raspberry no VS Code. Preserve ACELGA_DEVICE_KEY com a chave
real que foi copiada do container no teste anterior. Acrescente ou ajuste:

```dotenv
ACELGA_API_URL=http://192.168.0.110:8000
ACELGA_ALLOW_LOCAL_HTTP=1
ACELGA_INTERVAL_SECONDS=60
ACELGA_MAX_QUEUE=100
```

A URL deve conter somente a origem, sem /api/v1. HTTP local e uma configuracao
explicita para este prototipo na rede privada. Ele nao cifra o trafego; uma
implantacao em rede nao confiavel precisa de HTTPS. Redirecionamentos HTTP e
proxies de ambiente nao sao usados no envio, para preservar o destino da chave.

O script le esse arquivo diretamente; nao e necessario executar source, instalar
python-dotenv ou colocar segredos na linha de comando. Nunca versione a chave.
O exemplo raspberry/.env.example contem somente valores de exemplo.

## Copiar para a Raspberry

Na conexao SSH da Raspberry (usuario bianca), execute:

```bash
mkdir -p /home/bianca/tcc-raspberry
chmod 700 /home/bianca/tcc-raspberry
```

Em uma aba PowerShell do Windows, fora do SSH, execute:

```powershell
cd C:\TCC\tcc
scp .\raspberry\capture_and_send.py .\raspberry\tcc-camera.service .\.env.raspberry bianca@192.168.0.109:/home/bianca/tcc-raspberry/
```

De volta ao terminal SSH da Raspberry:

```bash
cd /home/bianca/tcc-raspberry
chmod 600 .env.raspberry
python3 capture_and_send.py --once
```

Esperado: TCC CAMERA AGENT V1, CAPTURADA com um UUID, e ENVIADA com o mesmo UUID e
HTTP 201 (ou 200 em caso de reenvio). Mensagens do rpicam-still podem aparecer.
Um teste bem-sucedido confirma a captura e o recebimento na API, ainda sem IA.

## Captura periodica e inicializacao automatica

Depois do primeiro envio bem-sucedido, no terminal SSH da Raspberry:

```bash
sudo cp /home/bianca/tcc-raspberry/tcc-camera.service /etc/systemd/system/tcc-camera.service
sudo systemctl daemon-reload
sudo systemctl enable --now tcc-camera.service
systemctl is-active tcc-camera.service
journalctl -u tcc-camera.service -n 25 --no-pager
```

Esperado: active. O agente captura ao iniciar e espera 60 segundos entre os ciclos,
alem do tempo gasto com captura e envio. Depois da demonstracao, ajuste
ACELGA_INTERVAL_SECONDS para 300 ou 900 se desejar menor frequencia.

Alternativa temporaria: python3 capture_and_send.py, sem --once, captura em loop
enquanto o terminal estiver aberto. Use Ctrl+C para interromper. Um lock impede
duas instancias simultaneas na mesma pasta.

Depois de editar .env.raspberry na Raspberry, aplique com:

```bash
sudo systemctl restart tcc-camera.service
```

Para parar a coleta, mantendo a configuracao:

```bash
sudo systemctl stop tcc-camera.service
```

Para desativar tambem o inicio automatico:

```bash
sudo systemctl disable --now tcc-camera.service
```

O servico foi preparado para o usuario bianca e /home/bianca/tcc-raspberry. Em
outra instalacao, ajuste User, WorkingDirectory e ExecStart antes de instalar.
O processo roda com as permissoes desse usuario, que ja conseguiu usar a camera.

## Fila e recuperacao

Cada captura recebe um UUID, um horario UTC e um SHA-256. O horario representa o
inicio do comando de captura, poucos segundos antes da exposicao; nao e uma medida
de sincronizacao precisa. Mantenha o relogio da Raspberry sincronizado pela rede.

Pastas locais:
- staging: arquivos em preparacao; so entram na fila apos JPEG e metadados estarem completos.
- pending: fotos aguardando confirmacao do servidor.
- rejected: arquivos preservados para revisao apos erro permanente ou captura incompleta.

O JPEG e os metadados sao sincronizados antes de publicar a pasta na fila. Se a
conexao ou a resposta falhar, o mesmo UUID, horario e conteudo sao reenviados.
O arquivo so sai da fila apos HTTP 200/201 com ID, SHA-256 e tamanho correspondentes.
Assim, perder a resposta depois do commit nao produz uma segunda captura no banco.

401/403 mantem a foto pendente: confira se a chave ainda e valida. 409/413/415/422
preservam a foto em rejected, com motivo.txt. Rede indisponivel, 429 e 5xx mantem
os arquivos pendentes. Erros de transmissao podem aparecer apenas como ausencia de
confirmacao; consulte os logs da API para detalhar o motivo nesses casos.

Por padrao, 100 pastas no total (pending + rejected + staging) pausam novas capturas;
arquivos antigos nao sao descartados para abrir espaco. O agente continua tentando
enviar a fila. Tambem pausa novas fotos quando ha menos de cerca de 210 MiB livres.
Fotos aceitas pelo servidor permanecem no storage do Windows; esse armazenamento
nao tem uma politica automatica de retencao nesta versao.

Para testar o reenvio sem capturar outra foto, pare antes o servico, se estiver ativo:

```bash
python3 capture_and_send.py --send-only
```

Teste manual opcional de indisponibilidade:
1. Pare o servico de captura na Raspberry.
2. No Windows, execute docker compose stop api.
3. Na Raspberry, execute python3 capture_and_send.py --once. A foto deve ficar PENDENTE.
4. No Windows, execute docker compose up -d --wait api.
5. Na Raspberry, execute python3 capture_and_send.py --send-only. Deve aparecer ENVIADA.
6. Reative o servico de captura se quiser continuar a coleta.

Nao recopie o codigo por cima de pastas de fila nem apague essas pastas para
resolver um erro. Examine os registros e preserve as fotos ainda nao confirmadas.

## Trocar de computador ou rede

No Windows, .env precisa conter API_LAN_IP com o IPv4 do computador atual, pois
compose.yaml publica a API nesse endereco alem do localhost. A .env.example da
raiz deve documentar essa variavel, por exemplo:

```dotenv
API_LAN_IP=SUBSTITUA_PELO_IPV4_DO_COMPUTADOR
```

Atualize tambem ACELGA_API_URL na .env.raspberry da Raspberry. Se o IP de algum
equipamento mudar, ajuste a regra TCC-API-Raspberry no firewall para os enderecos
atuais. Os IPs 192.168.0.110 (Windows) e 192.168.0.109 (Raspberry) sao os confirmados
nesta sessao; nao sao valores universais nem enderecos garantidos pelo DHCP.

## Git e verificacao

No Windows, acrescente ao readme.md um link para docs/raspberry.md. Versione
raspberry/capture_and_send.py, raspberry/tcc-camera.service, raspberry/.env.example,
raspberry/.gitignore, docs/raspberry.md, as alteracoes de compose.yaml, .env.example
da raiz e readme.md. Nao versione .env.raspberry nem as imagens.

Verificacao local: 10 testes passaram com Python 3.12. Camera simulada, fila real
em disco e uma chamada real ao Uvicorn 0.54.0 com as rotas de capturas. A prova de
reenvio usou SQLite de teste e autenticacao de apoio: a resposta do primeiro envio
foi descartada apos o commit, e o segundo foi aceito sem novo registro. Tambem
foram testados indisponibilidade, limite da fila, arquivos corrompidos, recuperacao
de staging e configuracao. A Camera Module 3 e o servico systemd precisam ser
verificados no equipamento fisico pelos passos acima.

Referencias oficiais:
- https://www.raspberrypi.com/documentation/computers/camera_software.html
- https://docs.python.org/3.13/library/urllib.request.html
