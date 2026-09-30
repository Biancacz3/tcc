# Monitoramento de pragas em acelga

Projeto de TCC para monitoramento de acelgas por imagens,
com captura pela Raspberry Pi e análise no computador.

## Ambiente

- Docker Desktop com WSL 2.
- Back-end: Python 3.13, FastAPI e Uvicorn.
- Banco de dados: PostgreSQL 17.
- Comunicação com o banco: SQLAlchemy 2 e Psycopg 3.
- Migrações: Alembic.
- Validação dos dados da API: Pydantic.

As versões das bibliotecas estão em backend/requirements.txt.

## Preparação

Na primeira configuração, crie .env a partir de .env.example.

Configure no .env:

- POSTGRES_DB: nome do banco.
- POSTGRES_USER: usuário do banco.
- POSTGRES_PASSWORD: senha exclusiva para o banco local.
- ADMIN_API_TOKEN: chave administrativa desta instalação.

Para gerar a chave administrativa:

`python -c "import secrets; print(secrets.token_urlsafe(32))"`

Copie o resultado para ADMIN_API_TOKEN.

Mantenha .env fora do Git. O arquivo .env.example contém
somente valores de exemplo.

## Executar

Com o Docker Desktop aberto, execute na raiz:

`docker compose up --build -d --wait`

Depois, aplique as migrações:

`docker compose exec api alembic upgrade head`

Verificação da API:
http://localhost:8000/api/v1/health

Verificação da conexão com o banco:
http://localhost:8000/api/v1/health/db

Documentação interativa:
http://localhost:8000/docs

Após alterar o código do back-end, reconstrua a imagem
com o comando de inicialização usando --build.

Para consultar os registros da API:

`docker compose logs --tail=80 api`

Para parar os serviços:

`docker compose stop`

## Migrações

Os arquivos ficam em backend/alembic/versions.

Migrações existentes:

- 0001_devices: cria a tabela devices.
- 0002_device_tokens: adiciona api_token_hash e sua
  restrição de unicidade.

Para consultar a versão aplicada:

`docker compose exec api alembic current`

Para verificar diferenças entre banco e modelos:

`docker compose exec api alembic check`

Mudanças futuras na estrutura terão novos arquivos de migração.

## Autenticação

As credenciais são enviadas no cabeçalho HTTP:

`Authorization: Bearer SUA_CHAVE`

No Swagger, clique em Authorize e informe somente a chave
no campo correspondente.

- AdminToken: chave administrativa do arquivo .env.
- DeviceToken: chave individual emitida para o dispositivo.

A autorização do Swagger vale para as requisições enviadas
por ele. Abrir uma rota diretamente na barra do navegador
não envia essa credencial.

## Dispositivos

As rotas ficam em backend/app/api/devices.py.
Os esquemas ficam em backend/app/schemas/device.py.
A autenticação fica em backend/app/security.py.

Cada requisição utiliza uma sessão própria do SQLAlchemy,
fechada ao terminar.

### Cadastrar

POST /api/v1/devices

Exige AdminToken.

Corpo de exemplo:

`{"name": "Raspberry 01"}`

O nome deve ter entre 1 e 100 caracteres após a remoção
dos espaços nas bordas. Campos extras são rejeitados.

Retorna HTTP 201 com id, name, is_active e created_at.
O dispositivo é criado como ativo.

Nomes podem se repetir. Cada cadastro recebe um UUID próprio.
Com autenticação válida, dados inválidos retornam HTTP 422.

### Listar

GET /api/v1/devices

Exige AdminToken.

Retorna uma lista ordenada por created_at e id.

Parâmetros opcionais:

- limit: de 1 a 100. Padrão: 50.
- offset: quantidade de registros a pular. Padrão: 0.

### Emitir ou substituir uma credencial

POST /api/v1/devices/{device_id}/token

Exige AdminToken.

Retorna HTTP 200 com device_id, token e token_type.

A chave contém 32 bytes de aleatoriedade gerados com secrets.
O banco armazena somente o hash SHA-256, em api_token_hash.

A chave completa é devolvida na resposta da emissão.
Guarde-a em local privado.

Emitir outra chave substitui a anterior, que deixa de funcionar.
Se a chave for perdida, o administrador pode emitir outra.

Dispositivo inexistente retorna HTTP 404.
Dispositivo inativo retorna HTTP 409 na emissão.

### Identificar o dispositivo autenticado

GET /api/v1/devices/me

Exige DeviceToken.

Retorna HTTP 200 com os dados do dispositivo autenticado.

Credencial ausente ou inválida retorna HTTP 401.
Dispositivos inativos também são rejeitados.

A chave do dispositivo não concede acesso administrativo.

## Testes das credenciais

- Emissão sem chave administrativa: HTTP 401.
- Emissão com chave administrativa válida: HTTP 200.
- Identificação sem chave de dispositivo: HTTP 401.
- Identificação com chave incorreta: HTTP 401.
- Identificação com chave válida: HTTP 200.
- Chave de dispositivo usada como administrativa: HTTP 401.
- Após nova emissão, chave anterior: HTTP 401.
- Após nova emissão, chave nova: HTTP 200.
- Hash armazenado no banco: 64 caracteres.

## Teste de disponibilidade do banco

A rota /api/v1/health/db executa SELECT 1 no PostgreSQL.

Quando funciona, retorna status ok e database connected.
Se o banco estiver indisponível, retorna HTTP 503.

Para testar a interrupção:

`docker compose stop db`

Para iniciar novamente:

`docker compose up -d --wait db`

Após iniciar o banco, a rota deve voltar a indicar connected.

## Dados locais

A API está publicada somente em 127.0.0.1:8000.

O PostgreSQL utiliza o volume postgres_data.

O GitHub sincroniza código, migrações e configurações de exemplo.
Os dados do banco permanecem no computador onde ele executa
e exigem backup e restauração para transferência.

As chaves completas dos dispositivos devem ser guardadas
separadamente e fora do Git. O banco contém apenas seus hashes.

## Progresso

- Repositório configurado no GitHub.
- FastAPI e PostgreSQL funcionando com Docker.
- Conexão com o banco usando SQLAlchemy.
- Verificações de saúde da API e do banco.
- Migrações gerenciadas com Alembic.
- Cadastro e listagem de dispositivos.
- Validação do nome do dispositivo.
- Proteção administrativa das operações de gerenciamento.
- Emissão e substituição de credenciais individuais.
- Identificação de dispositivo por sua credencial.
- Script inicial da Raspberry em raspberry/capture_and_send.py.
- Recebimento de imagens, interface e IA em desenvolvimento.
Captura de imagens: [configuracao e testes](docs/capturas.md).
