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

No .env, configure:

- POSTGRES_DB: nome do banco.
- POSTGRES_USER: usuário do banco.
- POSTGRES_PASSWORD: senha exclusiva para o banco local.
- ADMIN_API_TOKEN: chave administrativa desta instalação.

Para gerar a chave administrativa, execute no computador:

`python -c "import secrets; print(secrets.token_urlsafe(32))"`

Copie o resultado para ADMIN_API_TOKEN no arquivo .env.

Mantenha .env fora do Git. O arquivo .env.example contém
somente valores de exemplo.

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

## Autenticação administrativa

As rotas de cadastro e listagem de dispositivos exigem
a chave definida em ADMIN_API_TOKEN.

A chave é enviada no cabeçalho HTTP:

`Authorization: Bearer SUA_CHAVE_ADMINISTRATIVA`

Na documentação interativa:

1. Clique em Authorize.
2. No campo AdminToken, cole somente a chave.
3. Clique em Authorize e feche a janela.
4. Execute as operações de dispositivos.

Uma chave ausente ou incorreta retorna HTTP 401.

Abrir a rota diretamente na barra do navegador não utiliza
a autorização configurada no Swagger.

A chave administrativa permite gerenciar os dispositivos.
A credencial própria da Raspberry será implementada
para o envio de imagens.

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

Exige autenticação administrativa.

Corpo de exemplo:

`{"name": "Raspberry 01"}`

O nome deve ter entre 1 e 100 caracteres após a remoção
de espaços no início e no fim. Campos extras são rejeitados.

O cadastro retorna HTTP 201 com id, name, is_active e created_at.
O dispositivo é criado como ativo.

Nomes podem se repetir. Cada cadastro recebe um UUID próprio.

Com autenticação válida, dados inválidos retornam HTTP 422.

### Listar

GET /api/v1/devices

Exige autenticação administrativa.

A resposta é uma lista de dispositivos, ordenada por
data de criação e identificador.

Parâmetros opcionais:

- limit: quantidade de registros, de 1 a 100. Padrão: 50.
- offset: quantidade de registros a pular. Padrão: 0.

Exemplo de caminho:

`/api/v1/devices?limit=50&offset=0`

## Testes da autenticação

- GET de dispositivos sem chave: HTTP 401.
- POST de dispositivos sem chave: HTTP 401.
- GET de dispositivos com chave incorreta: HTTP 401.
- GET de dispositivos com chave correta: HTTP 200.
- POST com chave correta e nome vazio: HTTP 422.

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

A API está publicada somente em 127.0.0.1:8000.

O PostgreSQL utiliza o volume postgres_data.

Os dados permanecem no computador em que o banco está executando.
O GitHub sincroniza o código, as migrações e as configurações
de exemplo. A transferência dos dados exigirá backup e restauração.

Cada instalação configura suas credenciais no próprio .env.

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
- Proteção das rotas de dispositivos com chave administrativa.
- Script inicial da Raspberry em raspberry/capture_and_send.py.
- Credenciais da Raspberry, capturas, interface e IA em desenvolvimento.