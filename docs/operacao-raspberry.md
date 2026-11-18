# Raspberry Pi do protótipo: o que pertence à versão de bancada

A Raspberry fotografa e envia; o detector praga V2 roda no servidor. Trocar o modelo de IA no notebook não exige atualizar nem reiniciar a placa. O agente simples `raspberry/capture_and_send.py` encontrado no repositório original faz parte de uma fase anterior: sozinho, não dirige o robô nem reproduz sua missão.

## Identificar a instalação que percorreu a pista

As fontes preparadas para a bancada incluem a base V25 e ajustes de aproximação, enquadramento na volta, fila local e recuperação da faixa. A revisão efetivamente instalada deve ser identificada na placa. A etiqueta indica qual planta deve ser fotografada; a identificação inclui também a passagem. No arquivo de percurso documentado abaixo, a ordem dos marcadores foi **10, 11, 20, 21 na ida e 21, 20, 11, 10 na volta**.

| Na pasta do protótipo | Papel |
| --- | --- |
| `tcc_integrado.py` e os módulos Python que ele importa | Coordenam movimento, posicionamento para foto, persistência da missão e envio. Copiar só o arquivo principal não basta. |
| `percurso_lados_corrigidos_v25.json` | Define as oito etapas. Nesta configuração, `marker_side` indica a posição **do marcador em relação à planta na imagem**: `right` na ida e `left` na volta. Esses lados dependem da montagem. |
| `calibracao_linha_v1r.json` e `centralizacao-camera.json` | Guardam as calibrações de faixa e enquadramento daquela montagem. Devem ser preservadas na placa; uma montagem diferente exige verificação própria. |
| `.env.raspberry` | Guarda o endereço do servidor e a credencial de envio do dispositivo. Configure localmente e mantenha o arquivo fora do Git. |
| `inspecoes/`, `pending/`, `staging/` e `rejected/` | Guardam fotos, estado da missão e envios. São dados operacionais: preserve-os em cópia privada se quiser manter o histórico. |

A extensão entregue para partida automática usa a unidade Linux `tcc-prototipo.service`. A conclusão das oito fotos no percurso não comprova, por si só, que essa extensão foi instalada ou habilitada na versão que está hoje na placa.

## Conferência sem executar a missão

No terminal **da Raspberry**, estes comandos identificam a versão de arquivos-chave e consultam o estado do serviço; não acionam os motores:

```bash
cd ~/tcc-raspberry
sha256sum tcc_integrado.py seguir_linha_v8_horario_lento.py registro_robo.py percurso_lados_corrigidos_v25.json
systemctl show tcc-prototipo.service -p LoadState -p ActiveState -p UnitFileState
```

Para preparar a coleta no diretório ativo, liste os arquivos de código e configuração, sem abrir as credenciais:

```bash
find . -maxdepth 1 -type f \( -name '*.py' -o -name 'percurso*.json' -o -name 'centralizacao-camera.json' -o -name 'calibracao_linha_v1r.json' \) -printf '%f\n' | sort
```

Separe `tcc_integrado.py`, os módulos locais usados por ele (inclusive pelo seguidor de faixa e pelos módulos de câmera), o percurso que a missão carregou e as duas calibrações. Se o serviço estiver instalado, a propriedade `FragmentPath` de `systemctl show tcc-prototipo.service -p FragmentPath` informa onde está o arquivo da unidade: confira se o comando configurado usa o mesmo programa e percurso. Revise localmente qualquer unidade antes de compartilhar; ela pode conter configurações específicas daquela máquina. Exclua versões antigas, scripts de correção, registros de testes, fotos, fila e qualquer arquivo com credenciais. Compare as fontes selecionadas com as da placa, especialmente se houver duas cópias com o mesmo nome.
