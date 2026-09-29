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

No .env, substitua a senha de exemplo por uma senha exclusiva
para o banco local. Mantenha .env fora do Git.

## Executar

Com o Docker Desktop aberto, execute na raiz:

`docker compose up --build -d --wait`

Depois, aplique as migrações:

`docker compose exec api alembic upgrade head`

Verificação da API: http://localhost:8000/api/v1/health

Verificação da conexão com o banco:
http://localhost:8000/api/v1/health/db

Documentação interativa: http://localhost:8000/docs

Após alterar o código do back-end, execute novamente o comando
de inicialização com --build para reconstruir a imagem.

Para consultar os registros da API:

`docker compose logs --tail=80 api`

Para parar os serviços:

`docker compose stop`

## Migrações

Os arquivos de migração ficam em backend/alembic/versions.

Para consultar a versão aplicada:

`docker compose exec api alembic current`

Para verificar diferenças entre o banco e os modelos:

`docker compose exec api alembic check`

A migração 0001_devices cria a tabela devices, com:

- id: identificador UUID.
- name: nome do dispositivo.
- is_active: indicação de ativação.
- created_at: data e hora de criação.

Mudanças futuras na estrutura terão novos arquivos de migração.

## Dispositivos

As rotas ficam em backend/app/api/devices.py.

Os esquemas de entrada e saída ficam em
backend/app/schemas/device.py.

Cada requisição utiliza uma sessão própria do SQLAlchemy,
fechada ao terminar.

### Cadastrar

POST /api/v1/devices

Corpo de exemplo:

`{"name": "Raspberry 01"}`

O nome deve ter entre 1 e 100 caracteres após a remoção
de espaços no início e no fim. Campos extras são rejeitados.

O cadastro retorna HTTP 201 com id, name, is_active e created_at.
O dispositivo é criado como ativo.

Nomes podem se repetir. Cada cadastro recebe um UUID próprio.

Dados inválidos retornam HTTP 422.

### Listar

GET /api/v1/devices

A resposta é uma lista de dispositivos, ordenada por
data de criação e identificador.

Parâmetros opcionais:

- limit: quantidade de registros, de 1 a 100. Padrão: 50.
- offset: quantidade de registros a pular. Padrão: 0.

Exemplo:
http://localhost:8000/api/v1/devices?limit=50&offset=0

### Acesso nesta etapa

A API está publicada somente em 127.0.0.1:8000.

A autenticação dos dispositivos será implementada antes
do envio de imagens pela rede.

## Teste de disponibilidade do banco

A rota /api/v1/health/db executa SELECT 1 no PostgreSQL.

Quando a consulta funciona, retorna status ok e database connected.
Se o banco estiver indisponível, retorna HTTP 503.

Para testar a interrupção:

`docker compose stop db`

Para iniciar novamente:

`docker compose up -d --wait db`

Após iniciar o banco, a rota deve voltar a indicar connected.

## Dados locais

O PostgreSQL utiliza o volume postgres_data.

Os dados permanecem no computador em que o banco está executando.
O GitHub sincroniza o código, as migrações e as configurações
de exemplo. A transferência dos dados exigirá backup e restauração.

## Progresso

- Repositório configurado no GitHub.
- API mínima com endpoint de verificação.
- PostgreSQL configurado com volume persistente.
- Conexão entre API e PostgreSQL usando SQLAlchemy.
- Verificação da comunicação com o banco.
- Alembic configurado e primeira migração aplicada.
- Estrutura da tabela devices criada.
- Cadastro e listagem de dispositivos pela API.
- Validação do nome do dispositivo.
- Script inicial da Raspberry em raspberry/capture_and_send.py.
- Autenticação, capturas, interface e IA em desenvolvimento.