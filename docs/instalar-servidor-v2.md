# Servidor V2 em outro computador

Este guia é para uma **instalação nova**. Se o objetivo é apenas consultar o painel que já funciona, basta usar o navegador na mesma rede do servidor. Se for preservar fotos, contas ou análises, faça a migração dos dados antes de tratar o novo computador como o servidor do protótipo.

## O que levar

| Item | Finalidade |
| --- | --- |
| Código do servidor da bancada, `compose.yaml`, `compose.ia.yaml`, `.env.example`, migrações e arquivos da interface | São necessários o painel com login, o processo `app.worker` e as migrações correspondentes. A versão antiga do GitHub com apenas dois serviços não reproduz a V2. |
| `artifacts/models/praga-v2.onnx` | É o resultado exportado do treinamento. Copie o arquivo validado da instalação de origem ou use uma distribuição da mesma versão e confira a assinatura abaixo. |
| Configuração `.env` | Crie na máquina de destino a partir do exemplo correspondente ao código da bancada. Preencha as credenciais e a configuração de acesso da nova instalação. Não inclua valores reais no Git. |
| Backup do PostgreSQL e a pasta `storage/` | Necessários apenas para transferir contas, capturas e histórico já existentes. As imagens estão nos arquivos; os vínculos com as plantas e os resultados estão no banco. Transfira ambos em privado e mantenha o servidor original até conferir a cópia. |

## Primeira instalação com banco novo

Use um computador com Docker e Compose que possa executar contêineres Linux. No Windows do projeto foi utilizado Docker Desktop. Execute os blocos abaixo no PowerShell, na raiz do código do servidor, e avance somente se o bloco anterior terminar sem erro.

1. Se não houver `.env`, crie o arquivo a partir do exemplo:

   ```powershell
   if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
   notepad .env
   ```

   Preencha os campos obrigatórios do Compose, como o banco, o usuário, a senha e, quando exigido, a chave administrativa interna. Na V2, o usuário do painel entra com e-mail e senha.

2. Confira se o código e o modelo são os esperados:

   ```powershell
   docker compose -f compose.yaml -f compose.ia.yaml config --services
   Get-FileHash -Algorithm SHA256 -LiteralPath '.\artifacts\models\praga-v2.onnx'
   ```

   Devem aparecer `api`, `db` e `worker`. A assinatura **SHA-256** do arquivo da bancada (um resumo do seu conteúdo) é `304b45d68d3ba833a0ec8f43c0f3ae47924a1682d1864c9ce26719cc68182083`. Maiúsculas e minúsculas são equivalentes. Nos dois serviços que executam o detector, `MODEL_PATH` deve apontar para `/app/models/praga-v2.onnx`. Se faltar o serviço, o arquivo ou a assinatura não coincidir, complete a cópia antes de subir o servidor.

3. Prepare o servidor e o banco vazio:

   ```powershell
   docker compose -f compose.yaml -f compose.ia.yaml build api worker
   docker compose -f compose.yaml -f compose.ia.yaml up -d --wait db
   docker compose -f compose.yaml -f compose.ia.yaml run --rm --no-deps api alembic upgrade head
   docker compose -f compose.yaml -f compose.ia.yaml run --rm --no-deps api python -m app.manage_users create --role admin
   docker compose -f compose.yaml -f compose.ia.yaml up -d api worker
   ```

   O Alembic cria as tabelas; o comando seguinte pede a primeira conta de acesso. Em um banco restaurado, mantenha as contas já existentes. Os comandos acima descrevem os componentes da instalação verificada; a transferência para o novo computador precisa ser testada nele.

4. Abra [o painel](http://localhost:8000/painel/), entre e solicite uma análise de uma foto. Na mesma rede, configure na Raspberry o endereço do novo servidor e uma credencial de dispositivo válida nesse banco. Para verificar a operação, confirme que a foto chegou e que uma **nova análise** foi concluída; abrir uma análise antiga só exibe o histórico.

A GPU foi usada no treinamento. As máquinas que apenas recebem e analisam fotos não precisam da GPU, de `best.pt`, da base IP102 ou do ambiente Python de treino.
