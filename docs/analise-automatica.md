# Análise automática e histórico

## Funcionamento

O upload autenticado grava o JPEG e cria um registro na fila do banco na mesma
transação. A confirmação HTTP de recebimento não significa que a IA terminou.
O serviço `worker` consulta essa fila, verifica a integridade do original,
executa a IA e grava o resultado. O painel acompanha o andamento sem botão de
análise na operação normal.

Estados: `queued`, `processing`, `retry`, `completed`, `failed`. Cada foto tem
no máximo cinco tentativas automáticas, com espera crescente. Se o processo
for interrompido, o trabalho pode ser retomado depois que vencer o lease de
180 segundos. A combinação captura/configuração tem unicidade no banco,
evitando duplicar resultados em reenvios e retomadas. Uma falha definitiva
aparece no painel e pode ser reagendada por uma conta administradora.

O worker funciona sem navegador aberto. Docker Desktop e os containers precisam
estar em execução no notebook. O banco e a pasta `storage` precisam persistir.
Execute as migrações antes de iniciar API e worker após a atualização.

## Identidade da planta

Cabeçalhos opcionais no POST `/api/v1/captures`:

| Cabeçalho | Exemplo |
| --- | --- |
| `X-Plant-Id` | `aruco-10` |
| `X-Plant-Name` | `Acelga 01` |
| `X-Marker-Id` | `10` |
| `X-Mission-Id` | UUID da missão |
| `X-Passage` | `1` |
| `X-Plant-Side` | `externo` |

`X-Capture-Id`, `X-Captured-At` e a credencial individual da câmera continuam
obrigatórios. Nos cabeçalhos, use texto sem acentos. A identidade é guardada
por foto, preservando a associação histórica. Não é inferida pelo nome da
câmera. Fotos antigas podem ficar sem planta identificada.

O reenvio idêntico pode completar campos de identidade anteriormente vazios.
Não pode alterar uma identidade já preenchida. Omissão de campos novos por um
cliente antigo não apaga dados. Não reenvie uma imagem com ID diferente apenas
porque a rede falhou.

## Consultas

- `GET /api/v1/captures/{id}`: metadados, status da fila e contagem disponível.
- `GET /api/v1/captures/{id}/analysis-job`: andamento e eventual erro.
- `POST /api/v1/captures/{id}/analysis-job/retry`: reagenda falha ou captura
  sem fila; não duplica job em andamento ou já concluído.
- `GET /api/v1/plants/positive-history`: histórico com uma linha por foto
  que teve detecção positiva. Aceita `from_date`, `to_date`, `plant_id`,
  `limit` e `offset`. Datas inclusivas no fuso `America/Sao_Paulo`.

Consultas exigem sessão autenticada; reagendar análise exige administrador.
O POST legado `/captures/{id}/analyses` continua disponível para ferramentas
administrativas e pode gerar uma análise com outra configuração de modelo.
O histórico não apaga um registro positivo porque uma análise posterior da
mesma foto teve resultado diferente.

## Alcance do modelo

O painel usa **Praga detectada** como categoria de exibição. O modelo incluído
na instalação atual continua sendo `pulgao-v1`, com uma classe `pulgao`.
O rótulo original, o hash dos pesos e o limiar são preservados nos resultados.
Essa mudança de tela não treina um detector de qualquer inseto, nem garante
reconhecimento de adesivos ou rejeição de tampas e pedras.

Não altere o nome de uma classe para simular treinamento. Novos pesos e regras
de seleção da planta precisam ser validados com fotos do protótipo antes da
troca do detector. Ausência de detecção não confirma ausência de insetos.

## Verificação

No PowerShell do notebook, dentro de `C:\TCC\tcc`:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml ps
docker compose -f compose.yaml -f compose.ia.yaml logs --tail 30 worker
```

O evento `ANALISE_CONCLUIDA` indica conclusão persistida, inclusive quando a
contagem é zero. `Modelo de IA indisponivel` exige conferir o ONNX em
`artifacts/models/pulgao-v1.onnx`, reiniciar o worker e reagendar a foto se
as tentativas já tiverem terminado.

Os testes de software usam SQLite e incluem ONNX sintético para contratos.
O teste opcional test_real_pipeline.py também foi executado com os pesos reais
e as duas fotos de desenvolvimento; ver evidencias/ia/fluxo-real.json no pacote.
Isso não valida generalização, hardware, Docker Desktop ou conexão com a Pi.

## Envio pelo painel e nova análise

O botão "Enviar foto" usa POST /api/v1/captures/manual com sessão administrativa
e CSRF. Recebe JPEG bruto e preserva o arquivo original. Nomes com acentos são
codificados em percent-UTF8 com X-Plant-Name-Encoding=percent-utf8. A identidade
do envio é reutilizada em uma tentativa repetida, evitando uma cópia duplicada
quando o primeiro envio terminou mas a resposta se perdeu.

POST /api/v1/captures/{id}/reanalyze enfileira a configuração atual para uma
captura antiga, sem apagar o histórico. Se essa configuração já foi analisada,
recupera-se seu resultado. Uma análise em andamento não é iniciada novamente.

GET /api/v1/dashboard/summary retorna totais globais. Os contadores de positivos
e de fotos para conferir podem se sobrepor: uma foto pode ter uma previsão
positiva e também regiões não identificadas. Os candidatos desconhecidos não
entram na contagem de bichos.
