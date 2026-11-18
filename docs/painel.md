# Painel de inspeção

O painel fica em `/painel/` no computador que hospeda o servidor. O operador entra com e-mail e senha. A credencial utilizada pela Raspberry para enviar capturas tem uma função separada.

1. Entre no painel e localize a captura pela planta e pela passagem.
2. Abra a fotografia para consultar seu histórico. Para usar o modelo ativo, envie uma foto nova ou peça uma **nova análise** da captura, se a opção estiver disponível para sua conta.
3. Aguarde o processamento pelo serviço `worker`. O servidor registra a versão do detector junto ao resultado; abrir um resultado antigo não executa novamente o modelo.
4. Compare as caixas com os insetos visíveis. “Praga detectada” indica uma detecção aceita. Zero detecções também é um resultado possível e não garante ausência de insetos.

Na máquina que já está configurada, veja [como conferir os serviços](../readme.md). Na bancada, o modelo confirmado é [praga V2](modelo-praga-v2.md).
