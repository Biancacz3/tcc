# Integração da IA com o painel

O treinamento foi feito com PyTorch e uma GPU. A aplicação recebe o resultado do treinamento em formato ONNX e executa a análise na CPU do servidor. O arquivo usado na bancada é `artifacts/models/praga-v2.onnx`.

A Raspberry envia a captura. Quando uma análise é solicitada, o serviço `worker` prepara regiões da imagem e consulta o detector. Uma região candidata só entra na contagem se a previsão passar pelos critérios da aplicação. O resultado fica associado à fotografia e à versão do modelo no banco.

- [Modelo V2: dados, conversão, métricas e assinatura do arquivo](modelo-praga-v2.md)
- [Instalar o servidor em outro computador](instalar-servidor-v2.md)
- [Fazer uma nova análise pelo painel](painel.md)

Os scripts antigos de exportação para pulgões refletem a primeira versão do projeto. Os scripts reprodutíveis da V2 ficam em `tools/ia_v2/`.
