# Monitoramento de pragas em acelga

Projeto de TCC para monitoramento de acelgas por imagens,
com captura pela Raspberry Pi e análise no computador.

## Ambiente

- Docker Desktop com WSL 2.
- Back-end: Python 3.13, FastAPI e Uvicorn.
- Banco de dados: PostgreSQL 17.
- Comunicação com o banco: SQLAlchemy 2 e Psycopg 3.

As versões das bibliotecas estão em backend/requirements.txt.

## Preparação

Na primeira configuração, crie .env a partir de .env.example.

No .env, substitua a senha de exemplo por uma senha exclusiva
para o banco local. Mantenha .env fora do Git.

## Executar

Com o Docker Desktop aberto, execute na raiz:

`docker compose up --build -d --wait`

Verificação da API: http://localhost:8000/api/v1/health

Verificação da conexão com o banco:
http://localhost:8000/api/v1/health/db

Documentação interativa: http://localhost:8000/docs

Para consultar os registros da API:

`docker compose logs --tail=80 api`

Para parar os serviços:

`docker compose stop`

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
O GitHub sincroniza o código e as configurações de exemplo.
A transferência dos dados exigirá backup e restauração.

## Progresso

- Repositório configurado no GitHub.
- API mínima com endpoint de verificação.
- PostgreSQL configurado com volume persistente.
- Conexão entre API e PostgreSQL usando SQLAlchemy.
- Rota de verificação da comunicação com o banco.
- Script inicial da Raspberry em raspberry/capture_and_send.py.
- Migrações com Alembic: próxima subetapa.
- Interface, captura integrada e IA: em desenvolvimento.    