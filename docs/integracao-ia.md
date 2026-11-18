# IA por regiões — versão 2

A API analisa pequenas regiões da planta. O JPEG recebido permanece intacto;
a orientação EXIF é aplicada em memória, antes da análise. As caixas retornadas
estão em pixels da imagem orientada, como exibida pelo navegador.

## Processamento

1. Verifica hash, dimensões, iluminação e detalhe disponíveis.
2. Separa a planta por cor e GrabCut. Se houver mais de uma planta, usa o
   marcador informado para associação; ambiguidade retorna inconclusivo.
3. Localiza regiões escuras com contraste local e limites de tamanho.
4. Executa o ONNX em recortes com contexto de 3 e 5 vezes o maior lado
   do candidato, limitados a 96–512 pixels.
5. Aceita previsões que correspondam a um candidato, caibam na máscara
   e não ocupem uma fração grande da planta. Elimina duplicatas.

A localização gera candidatos; somente previsões aceitas do modelo entram em
`detection_count`. Uma imagem pode ser positiva e ter regiões não identificadas.

## Resultados

| analysis_state | Significado |
| --- | --- |
| positive | Pelo menos uma previsão aceita do modelo. |
| review | Candidatos encontrados, nenhum reconhecido. |
| no_detections | A análise utilizável não encontrou candidatos. |
| inconclusive | Imagem, associação ou orçamento insuficiente para concluir. |
| legacy | Resultado anterior à atualização, preservado no histórico. |

`candidates` contém id, xyxy e status detected/unidentified.
`unidentified_count`, `warnings`, `method` e `plant_bbox` complementam o resultado.
O SHA da configuração inclui pesos, parâmetros de regiões, escalas e limiares.

## Modelo e avaliação

ONNX FP32, entrada [1,3,640,640], saída [1,5,8400], uma classe `pulgao`.
Pesos SHA256: `cde9133603b04fab3f0f6f1a244e19adbebb398ce4a3900e51f0c252a234fb60`.
Limiar do modelo 0,35, NMS 0,70 e correspondência candidato/caixa IoU 0,30.
O limiar não foi reduzido para transformar toda mancha em detecção.

Nas duas fotos de desenvolvimento, houve 7/8 e 3/6 correspondências com os
objetos de simulação visíveis, sem previsões fora dessas referências.
As referências manuais foram usadas para comparar resultados depois da
inferência; não fornecem posições ao detector.

Esse ensaio não é validação independente. Não houve treinamento novo; o modelo
não se tornou um detector universal de insetos. Rejeição de tampas, pedras e
folhas secas ainda precisa de fotos e avaliação próprias. A máscara pode deixar
partes claras ou sombreadas fora da região analisada.

## Operação

Novos uploads são analisados pelo worker sem navegador aberto.
Fotos já analisadas podem usar "Fazer nova análise", que enfileira a configuração
atual e preserva resultados anteriores. Com a mesma configuração, a análise
existente é reutilizada. Consulte analise-automatica.md.

Após trocar pesos compatíveis, reinicie API e worker. O instalador valida a
estrutura do modelo, sem substituir um ONNX existente.
