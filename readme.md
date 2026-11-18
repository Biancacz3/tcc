# Monitoramento de pragas em acelgas — SmartFarm

Protótipo de TCC para apoiar a inspeção visual de acelgas. Um robô segue uma faixa na bancada e fotografa quatro plantas na ida e na volta. São oito registros previstos por missão. Cada foto chega ao computador identificada por planta e passagem; o operador pode solicitar uma análise e consultar o histórico no navegador.

**Versão da bancada:** em 18/11/2026, a API e o serviço de análise carregaram o mesmo detector, `praga-v2-regioes`, e uma análise foi registrada no banco com essa versão. A instalação do computador e o conteúdo de um clone do GitHub são coisas diferentes. A revisão anterior do repositório ([`6013662`](https://github.com/Biancacz3/tcc/tree/60136629cfcc061f94736916efe634749fbb5d0d)) tinha apenas `api` e `db` no Compose, um agente de captura simples na Raspberry e instruções da primeira versão do painel. Para reproduzir a bancada, é preciso também o código com o serviço `worker`, o programa integrado do robô e o arquivo ONNX da V2.

## Como uma foto chega ao painel

| Etapa | O que acontece |
| --- | --- |
| Captura | Os sensores de linha orientam o deslocamento; os marcadores visuais ArUco identificam as plantas. A Raspberry Pi 3 Model B para e fotografa com a Camera Module 3. O programa principal da bancada é `tcc_integrado.py`. |
| Envio | A Raspberry envia a captura por HTTP na rede Wi-Fi local, usando uma credencial própria. Se não receber confirmação do servidor, guarda a captura na fila local para reenvio. |
| Armazenamento | A API FastAPI recebe a captura. O JPEG fica no armazenamento do servidor e o PostgreSQL guarda a identificação da planta, os registros da captura e as análises. |
| Análise | O `worker` é o serviço que processa a fila de análises. A solução usa OpenCV para preparar regiões da imagem e ONNX Runtime para executar o detector na CPU. O modelo possui uma única categoria, **praga**, treinada com exemplos de pulgões e besouros. |
| Consulta | O operador acessa o painel com e-mail e senha. Pode abrir a foto, visualizar as caixas de detecção e pedir uma nova análise. A credencial da Raspberry serve para enviar fotos; ela não é o login do operador. |

**Zero detecções** significa que o sistema não aceitou nenhuma região naquela imagem. Não prova ausência de insetos na planta. A demonstração usou plantas de bancada; as métricas do detector foram obtidas em imagens reservadas da base IP102.

## Usar no computador já configurado

Abra o Docker Desktop. Se os serviços já iniciarem automaticamente, basta abrir [o painel local](http://localhost:8000/painel/). Não é necessário treinar nem exportar o modelo a cada uso.

Para conferir o servidor, abra o PowerShell na pasta que contém os dois arquivos Compose:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml ps
Invoke-RestMethod 'http://localhost:8000/api/v1/health'
Invoke-RestMethod 'http://localhost:8000/api/v1/health/db'
```

Na instalação V2, espere encontrar `api`, `db` e `worker`. A primeira consulta verifica a API e a segunda verifica a conexão com o banco. Se estiverem ativos, mas houver análises pendentes, consulte `docker compose -f compose.yaml -f compose.ia.yaml logs --tail 60 worker`.

Em outro computador ou celular na mesma rede, use `http://IP_DO_SERVIDOR:8000/painel/`, substituindo `IP_DO_SERVIDOR` pelo IPv4 do computador que executa o Docker. O servidor precisa aceitar conexões da rede local. Nesse outro aparelho, `localhost` apontaria para o próprio aparelho.

## Instalar em outro computador

Veja o [guia para outro computador](docs/instalar-servidor-v2.md) para conferir o modelo, iniciar uma instalação nova e distinguir os arquivos do projeto dos dados privados. **Para analisar fotos não é preciso instalar CUDA, ter placa NVIDIA ou baixar o IP102.** O modelo já foi treinado; o servidor utiliza seu arquivo exportado.

Para a preparação das imagens, treinamento, avaliação e versão dos pesos, consulte [Modelo de IA: praga V2](docs/modelo-praga-v2.md). Para identificar o programa e a configuração da placa, veja [Operação da Raspberry](docs/operacao-raspberry.md).

Para quem mantém esta instalação: [como trazer para o Git a cópia do notebook e da Raspberry](docs/sincronizar-bancada.md).
