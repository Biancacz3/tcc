# Detector de pragas em acelga — V2

O detector localiza possíveis insetos e marca suas posições na foto. A resposta tem **uma única categoria: `0: praga`**. Pulgões e grupos de besouros foram reunidos nessa categoria; a resposta não identifica a espécie. A Raspberry captura e envia a foto. O computador que hospeda o painel faz a análise pelo ONNX Runtime, usando a CPU.

## Modelo em operação

- Peso usado pelo servidor: `artifacts/models/praga-v2.onnx`.
- Nome gravado nas análises que usam o detector com a seleção de regiões: `praga-v2-regioes`.
- SHA-256 do ONNX confirmado na API e no serviço de análise: `304b45d68d3ba833a0ec8f43c0f3ae47924a1682d1864c9ce26719cc68182083`.

Para conferir o arquivo no PowerShell, a partir da raiz do projeto:

```powershell
(Get-FileHash .\artifacts\models\praga-v2.onnx -Algorithm SHA256).Hash
```

O código identifica o arquivo exato, sem distinção entre letras maiúsculas e minúsculas. Na implantação de 18/11/2026, uma nova análise foi gravada pelo servidor com esse modelo. O registro foi conferido pelo nome e pela assinatura do modelo. A contagem foi zero; sem uma anotação humana da foto, esse número sozinho não mede acerto. Para instalar o mesmo modelo em outro computador, use o ONNX já conferido: não é preciso repetir o treinamento nem copiar o dataset.

## Preparação das imagens

A fonte é o [conjunto de detecção IP102](https://github.com/xpwu95/IP102). O preparador lê as imagens e os arquivos XML que identificam os insetos e as caixas ao redor deles. Para esta versão, foram selecionados o rótulo XML **24** (pulgões) e os grupos de besouros, incluindo grupos larvais, de rótulos **10, 14, 16, 25, 37, 42, 43, 44, 49, 50, 51, 62, 68, 96, 99 e 100**. São os números contidos nos XMLs da base usada neste trabalho; o código não deduz a classe pelo nome do JPEG. A auditoria visual de alguns rótulos serviu para conferência e não define sozinha a seleção final.

Cada caixa do XML se torna uma linha de TXT: `0 centro_x centro_y largura altura`. O centro é o ponto médio entre os cantos. Os valores horizontais são divididos pela largura da foto e os verticais pela altura. A identidade da imagem é verificada pelo ID e pelos pixels. O teste original foi preservado e o manifesto da V1 manteve as divisões prévias dos exemplos reaproveitados. Dos exemplos novos de treino/validação, cerca de 20% foram reservados para validação por combinação de rótulos, com semente 42.

| Divisão | Imagens | Insetos anotados | Uso |
|---|---:|---:|---|
| Treino | 3.146 | 3.929 | Ajustar os pesos |
| Validação | 786 | 953 | Acompanhar o treino e selecionar pesos |
| Teste | 981 | 1.189 | Avaliar os pesos escolhidos |
| **Total** | **4.913** | **6.071** | |

## Como repetir o experimento

Os comandos abaixo são para PowerShell na raiz do repositório, com o ambiente Python de treino ativo. Troque os caminhos de exemplo pelas pastas reais, fora do repositório. A base original precisa ter `Annotations`, `JPEGImages` e `ImageSets/Main`, e a base antiga deve conter o `manifesto.json` da V1 para reproduzir a mesma divisão. O preparador usa Pillow. A execução registrada usou Python 3.13.3, Ultralytics 8.3.203 e PyTorch 2.8.0+cu126 com uma GeForce GTX 1050 Ti de 4 GB. A máquina que roda apenas o painel não precisa desse ambiente de treino.

```powershell
$origem = 'D:\dados\VOC2007'
$anterior = 'D:\dados\ip102-pulgao-v1'
$base = 'D:\dados\ip102-praga-v2'
$treinos = 'D:\experimentos'
$ids = 10,14,16,24,25,37,42,43,44,49,50,51,62,68,96,99,100
python .\tools\ia_v2\preparar_praga_v2.py preparar $origem $base --classes-xml @ids --base-anterior $anterior
```

Use uma pasta de saída nova. O preparador produz `data.yaml`, fotos e rótulos separados por divisão, `manifesto.json` (origem, divisão e identificação pelos pixels), `resumo.json` (contagens) e `exclusoes.json` (motivos de exclusão). O comando abaixo inicia um novo treinamento por transferência de aprendizado a partir de **yolo11n.pt**. Na execução registrada foram concluídas 30 épocas em aproximadamente 3 h 08 min.

```powershell
yolo detect train model=yolo11n.pt "data=$base\data.yaml" "project=$treinos" name=praga-v2 epochs=30 imgsz=640 batch=2 workers=0 device=0 amp=False
```

A execução do projeto salvou os pesos em `praga-v22/weights/best.pt`. Em outra máquina, use a pasta que o seu treino informar em `Results saved to`; o Ultralytics pode acrescentar um número ao nome quando a pasta já existe. Para avaliar no **teste reservado**:

```powershell
$pesos = Join-Path $treinos 'praga-v22\weights\best.pt'
yolo detect val "model=$pesos" "data=$base\data.yaml" split=test imgsz=640 batch=2 workers=0 device=0 "project=$treinos" name=teste-praga-v2
```

## Resultados

| Conjunto do IP102 | Precisão | Revocação (recall) | mAP50 | mAP50–95 |
|---|---:|---:|---:|---:|
| Validação (786 imagens; `best.pt`) | 88,9% | 81,4% | 89,0% | 49,5% |
| **Teste (981 imagens; `best.pt`)** | **86,8%** | **83,9%** | **89,6%** | **49,9%** |

A precisão indica a fração de detecções corretas entre as avaliadas; a revocação indica a fração dos objetos anotados que foram encontrados. mAP50 mede a qualidade das detecções com sobreposição mínima de 50% entre caixas; mAP50–95 reúne limiares de 50% a 95%. Estas métricas são do detector PyTorch nas imagens do IP102. O conjunto preparado só tem imagens positivas. A câmera, os insetos artificiais e os recortes que o servidor analisa na bancada compõem outro cenário; para estimar seu desempenho são necessárias fotos próprias anotadas, inclusive fotos sem insetos.

Na instalação do protótipo, o `best.pt` foi convertido para ONNX com entrada de 640 × 640, lote 1, FP32 e opset 17. A comparação entre PyTorch e ONNX usa os mesmos pixels de cinco imagens de validação para verificar a conversão; não substitui a avaliação de qualidade feita nas 981 imagens de teste.

### Exportar novamente, se for necessário

No ambiente do treinamento, a ferramenta de exportação requer `onnx` e `onnxruntime`. O comando escreve em um destino novo, fora da instalação. O argumento `--origem` é opcional: permite usar os pixels da base VOC original, conferidos com o manifesto da V2, caso as cópias de validação não estejam legíveis.

```powershell
python -m pip install onnx==1.19.0 onnxruntime==1.23.0
python .\tools\ia_v2\exportar_praga.py --pesos $pesos --dataset $base --destino "$treinos\exportacao\praga-v2.onnx" --origem $origem
```

Exporte somente pesos de procedência conhecida. Um novo treinamento ou exportação pode produzir um arquivo com assinatura diferente; guarde o relatório de equivalência com cada versão. A exportação não altera o Docker, o painel nem o banco. O instalador de atualização usado na bancada foi feito para migrar uma instalação anterior em execução; para uma instalação nova, consulte o README principal.
