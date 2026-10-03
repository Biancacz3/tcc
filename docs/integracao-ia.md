# IA no painel — atualização de 03/10/2026

Esta atualização acrescenta análise manual de uma captura, caixas sobre a foto,
resultado e histórico no PostgreSQL. O JPEG original permanece intacto. Não há
análise automática de todas as capturas nesta versão.

A NVIDIA foi usada no treino. O backend executa o detector exportado em ONNX FP32
no processador, com dois threads, dentro do Docker. Isso permite usar a mesma
integração em computadores sem NVIDIA. Não mede o desempenho em uma plantação.

## 1. Aplicar os arquivos

Salve **tcc-atualizacao-ia.zip** em **C:\TCC**. Não confunda com o arquivo
**tcc-integracao-ia.zip** que você enviou para revisão.

No PowerShell do computador de casa:

```powershell
$ErrorActionPreference = "Stop"
cd C:\TCC\tcc
if (!(Test-Path C:\TCC\tcc-atualizacao-ia.zip)) { throw "Baixe o ZIP em C:\TCC primeiro." }
$backupIA = "C:\TCC\backup-antes-ia-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Path $backupIA | Out-Null
Copy-Item .\backend "$backupIA\backend" -Recurse
Copy-Item .\compose.yaml "$backupIA\compose.yaml"
Expand-Archive -LiteralPath C:\TCC\tcc-atualizacao-ia.zip -DestinationPath . -Force
```

O ZIP inclui somente os arquivos novos/modificados. Não substitui compose.yaml,
.env, fotografias, pesos do treino ou migrações 0001–0003.

## 2. Exportar e conferir o modelo treinado

Execute uma linha por vez; se houver erro, pare e envie a mensagem.

```powershell
C:\TCC\venvs\tcc-ia\Scripts\python.exe -m pip install onnx==1.19.0 onnxruntime==1.23.0
C:\TCC\venvs\tcc-ia\Scripts\python.exe .\tools\exportar_modelo_ia.py
```

O script usa `C:\TCC\treinos\pulgao-v1\weights\best.pt`, exporta uma cópia
em diretório temporário e compara caixas e pontuações em cinco fotografias de
**validação**, usando os mesmos pixels e parâmetros no PyTorch e no backend.
Não treina novamente. Não usa o teste para ajustar o limiar.

Resultado esperado: **MODELO EXPORTADO E VERIFICADO**, após cinco linhas **OK**.
Arquivos gerados:

- `artifacts/models/pulgao-v1.onnx`
- `artifacts/models/verificacao-exportacao.json`

A comparação permite até 1 pixel por coordenada e 0,002 por pontuação. Uma
quantidade diferente de detecções ou divergência acima do limite interrompe a
instalação do modelo. Não ignore esse erro. O teste é de equivalência técnica,
não uma nova medição de qualidade do detector.

## 3. Construir e migrar

Abra o Docker Desktop. Execute cada comando separadamente, aguardando o término.
Se um comando falhar, não continue para o próximo.

```powershell
cd C:\TCC\tcc
docker compose -f compose.yaml -f compose.ia.yaml up -d --wait db
docker compose -f compose.yaml -f compose.ia.yaml build api
```

Antes da migração, faça uma cópia do banco. O dump é gravado como arquivo binário
dentro do container, sem redirecionamento de binário no PowerShell:

```powershell
docker compose exec db pg_dump -U tcc_admin -d tcc_pragas -Fc -f /tmp/antes-ia.dump
$dumpIA = "C:\TCC\banco-antes-ia-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
docker compose cp db:/tmp/antes-ia.dump $dumpIA
```

Depois:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml run --rm --no-deps api alembic upgrade head
docker compose -f compose.yaml -f compose.ia.yaml up -d --no-deps api
docker compose -f compose.yaml -f compose.ia.yaml exec api alembic current
docker compose -f compose.yaml -f compose.ia.yaml exec api alembic check
```

Resultados esperados: `0004_analyses (head)` e `No new upgrade operations detected.`
A migração acrescenta a tabela `analyses`, sem alterar as capturas existentes.

## 4. Usar o painel

Abra http://localhost:8000/painel/ e pressione **Ctrl+F5** para carregar os arquivos
novos. Entre com sua chave administrativa, abra uma foto e clique em **Analisar
com IA**. A primeira análise inclui o carregamento do modelo e pode levar mais
tempo. As próximas reutilizam o modelo já carregado.

- **Mostrar marcações** alterna entre a foto original e as caixas.
- O resultado traz quantidade, pontuações, modelo, limiar e data/hora.
- Fechar e reabrir a foto recupera o resultado salvo.
- Repetir o pedido com a mesma configuração retorna o registro existente.
- Análises de modelos/configurações diferentes ficam no histórico; a interface
  mostra até as 20 mais recentes.
- Nenhuma detecção significa apenas que não houve detecção acima do limiar.
  Não significa planta saudável ou ausência de pragas.

As fotografias atuais podem não conter pulgões. Nesse caso é esperado não haver
caixas; não há detecções simuladas. A análise usa somente o seu modelo real.

## Configuração e rastreabilidade

Limiar inicial: **0,35**, escolhido como ponto de partida experimental, sem ajuste
no conjunto de teste. NMS IoU: **0,70**. Entrada: **640 × 640**, letterbox RGB.
Até 300 caixas. Guarda-se hash SHA-256 do ONNX, da imagem e da configuração,
pontuações, caixas em pixels da imagem original, dimensões, tempo e data/hora.
O hash da configuração inclui a versão do processamento.

O arquivo instalado deve ser YOLO11 de uma classe chamada `pulgao`, FP32,
entrada `[1,3,640,640]`, saída `[1,5,8400]`, sem NMS embutido. Outros formatos
são rejeitados. Fotografias com rotação EXIF diferente de 1 precisam ser
normalizadas antes de um novo envio; as capturas da Raspberry já testadas não
precisam dessa transformação.

API, sempre com **chave administrativa**:

- `POST /api/v1/captures/{id}/analyses`: cria (201) ou recupera (200) o resultado.
- `GET /api/v1/captures/{id}/analyses?limit=20`: histórico, mais recente primeiro.

Uma análise por vez em cada processo da API. O Compose atual inicia um processo.
O limite impede consumo simultâneo excessivo de memória; pedidos concorrentes
recebem 503 com orientação para tentar depois. Falha de modelo, arquivo ou banco
não é apresentada como sucesso.

## Reiniciar e diagnosticar

Use os **dois arquivos Compose** ao recriar a API, para manter o modelo montado:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml up -d
```

Depois de substituir o modelo por uma nova versão, reinicie a API:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml restart api
```

Se a análise falhar:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml logs --tail 60 api
```

A falta do ONNX não impede a galeria de abrir; apenas a análise retorna erro.
A montagem adicional é somente leitura e não modifica as portas definidas no
compose.yaml de cada computador.

## Validação e limites

O pacote inclui testes de API com SQLite e um ONNX **sintético somente nos testes**:
autenticação, persistência, repetição, falhas, histórico e decodificação de caixas.
O ONNX sintético não é incluído nem instalado como modelo do sistema. A integração
real depende da exportação/verificação no seu computador, onde está o best.pt.
O comando `alembic check` verifica a migração no seu PostgreSQL.

Seu teste anterior do modelo PyTorch: 174 imagens, 278 objetos, precisão 0,782,
recall 0,748, mAP50 0,834 e mAP50–95 0,453. Essas métricas não são uma garantia
para as capturas da Raspberry nem se referem especificamente ao limiar 0,35.
O conjunto atual só contém imagens com pulgões e tem anotações incompletas em
algumas fotos. Antes de uso real, avaliar imagens sem pulgões, outros insetos,
plantas reais e diferentes condições de captura.

Para preservar tudo, mantenha o código no Git e faça backup separado de
`artifacts/models`, `C:\TCC\treinos`, `storage` e do dump PostgreSQL. Os pesos e
fotos não precisam ir para o Git; confirme que `/artifacts/models/` está ignorado.
