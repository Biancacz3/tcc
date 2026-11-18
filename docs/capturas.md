# Capturas e reenvio

A Raspberry fotografa com as rodas paradas. Cada registro identifica a planta e a passagem. O servidor recebe a imagem; o arquivo JPEG fica no armazenamento e o banco relaciona a captura à planta e aos resultados. Uma foto recebida e uma análise concluída são eventos diferentes.

## Quando o envio falha

Se a Raspberry não obtiver a confirmação do servidor, mantém a captura na fila local e tenta enviar novamente com o mesmo identificador. Isso permite reconhecer um reenvio da mesma foto. Antes de repetir uma missão, confira se o servidor está ativo, se o dispositivo está configurado para o servidor correto e se ainda há capturas pendentes. Preserve a fila se quiser recuperar os registros.

Para verificar a instalação da bancada, use o [guia do servidor](instalar-servidor-v2.md). Endereços de cada instalação e valores de credenciais são configurados localmente.
