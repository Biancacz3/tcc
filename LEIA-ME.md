# Diagnóstico offline da foto de demonstração

Este pacote analisa o JPEG original capturado pela Raspberry com o ONNX já
instalado no notebook. Compara a foto inteira, um recorte aproximado da acelga
artificial e duas metades com sobreposição. Ele registra pontuações e caixas do
modelo, sem alterar API, banco, pesos, painel ou carrinho. As caixas são
**previsões experimentais**, não confirmação de pulgões reais.

No PowerShell do notebook, baixe o ZIP em `C:\TCC` e execute:

```powershell
scp bianca@raspberrypi.local:/home/bianca/tcc-raspberry/diagnostico-integrado.jpg C:\TCC\diagnostico-integrado.jpg
Expand-Archive -LiteralPath C:\TCC\diagnostico-adesivos-v1.zip -DestinationPath C:\TCC\tcc -Force
cd C:\TCC\tcc
C:\TCC\venvs\tcc-ia\Scripts\python.exe .\tools\diagnosticar_foto_adesivos.py C:\TCC\diagnostico-integrado.jpg --modelo C:\TCC\tcc\artifacts\models\pulgao-v1.onnx
```

O script cria uma pasta `C:\TCC\diagnostico-adesivos-*` e informa o caminho.
Confira `comparacao.jpg` para confirmar se o recorte contém a planta inteira.
Envie `resumo.json` e `comparacao.jpg` para interpretar o resultado.

Se houver caixas somente nos recortes, o tamanho da planta na entrada de 640
pixels é um fator e podemos testar recortes na API. Se não houver caixas nem
nos recortes, o modelo atual provavelmente precisa de exemplos rotulados dos
adesivos e imagens sem adesivos para a demonstração. Pontuações baixas não
equivalem a pulgões; reduzir apenas o limiar pode causar falsos positivos.

O script depende de Pillow, NumPy, OpenCV e onnxruntime no ambiente Python
`C:\TCC\venvs\tcc-ia`, além do arquivo `backend/app/inference.py` do projeto.
Não executa requisições de rede nem grava resultado no PostgreSQL.
