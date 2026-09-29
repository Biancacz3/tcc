# Monitoramento de pragas em acelga

Projeto de TCC para monitoramento de acelgas por imagens,
com captura pela Raspberry Pi e análise no computador.

## Ambiente

- Docker Desktop com WSL 2.
- Back-end: Python 3.13, FastAPI e Uvicorn.
- Banco de dados: PostgreSQL 17.

## Preparação

Crie um arquivo .env a partir de .env.example.

No .env, substitua a senha de exemplo por uma senha exclusiva
para o banco local. Mantenha .env fora do Git.

## Executar

Com o Docker Desktop aberto, execute na raiz:

`docker compose up --build -d`

Verificação da API: http://localhost:8000/api/v1/health

Documentação interativa: http://localhost:8000/docs

Para verificar o banco:

`docker compose ps db`

Para parar os serviços:

`docker compose stop`

## Dados locais

O PostgreSQL utiliza o volume postgres_data.

Os dados do banco permanecem no computador em que ele está
executando. O GitHub sincroniza o código e as configurações
de exemplo. A transferência dos dados exigirá backup e restauração.

## Progresso

- Repositório configurado no GitHub.
- API mínima com endpoint de verificação.
- PostgreSQL configurado com volume persistente.
- Script inicial da Raspberry em raspberry/capture_and_send.py.
- Conexão entre API e banco e migrações: próximas subetapas.
- Interface, captura integrada e IA: em desenvolvimento.