# Monitoramento de pragas em acelga

Projeto de TCC para monitoramento de acelgas por imagens,
com captura pela Raspberry Pi e análise no computador.

## Ambiente

- Docker Desktop com WSL 2.
- Back-end: Python 3.13, FastAPI e Uvicorn.

## Executar

Com o Docker Desktop aberto, execute na raiz do projeto:

`docker compose up --build`

Verificação da API: http://localhost:8000/api/v1/health

Documentação interativa: http://localhost:8000/docs

Para interromper a execução, pressione Ctrl+C no terminal.

## Progresso

- Repositório configurado no GitHub.
- API mínima com endpoint de verificação.
- Script inicial da Raspberry em raspberry/capture_and_send.py.
- Integração da câmera, banco, interface e IA em desenvolvimento.