# Trazer para o Git o código que está em uso

A instalação que funciona é a referência para o servidor e para o painel. A Raspberry tem outra pasta de trabalho. Atualizar a documentação ou copiar um modelo não transfere automaticamente esses dois ambientes.

## 1. Separar os arquivos do servidor

Baixe `tools/coletar_versao_local.py` desta revisão ou abra uma cópia separada do repositório que contenha a ferramenta. No computador que executa a API e o worker, com o script na pasta atual, por exemplo:

```powershell
$destino = Join-Path ([Environment]::GetFolderPath('Desktop')) ("tcc-publicacao-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".zip")
python .\coletar_versao_local.py --repo 'C:\TCC\tcc' --saida "$destino"
```

Use o Python do projeto se `python` não estiver no PATH. O ZIP é criado fora do projeto. O coletor inclui nomes e tipos de arquivo selecionados: fonte, interface, migrações, configurações públicas, documentação, testes e o arquivo ONNX **se o seu SHA-256 bater com o da V2**. Não executa a aplicação, não modifica o índice do Git e não publica nada. A saída informa se o modelo entrou.

Abra o ZIP e confira o `inventario-publicacao.json`. Um item em `omitidos` indica que ele foi considerado, mas ficou de fora; arquivos de tipos ou pastas fora da lista também ficam de fora. Uma omissão não é uma instrução para excluir arquivos do Git. O filtro de conteúdo ajuda, mas não substitui a revisão humana. Para um arquivo legítimo omitido, revise uma **cópia** em separado: retire dados reais, preserve o código. Um `.env.example` deve conter apenas exemplos; compare-o separadamente.

## 2. Conferir a Raspberry

Se os módulos ativos não estiverem no notebook, faça a coleta também na placa. Com uma cópia do mesmo script na pasta pessoal e usando o diretório efetivo do programa:

```bash
python3 ~/coletar_versao_local.py --raspberry ~/tcc-raspberry --saida ~/tcc-publicacao-raspberry.zip
```

Esse comando lê os arquivos selecionados. Ele não inicia a missão. O arquivo de serviço instalado pelo sistema pode estar fora dessa pasta; veja [como conferir na placa](operacao-raspberry.md).

## 3. Integrar

Compare o conteúdo selecionado com a branch de atualização antes de levar a versão para a `main`. Em particular, confira os arquivos usados pelo servidor, as migrações, o login, os textos do painel, os dois arquivos Compose, o modelo e o percurso carregado pela Raspberry. Preserve alterações que já estejam no notebook ou no repositório. O ZIP e o inventário são intermediários: publique as fontes revisadas, não o ZIP. Fotos, banco de dados, credenciais e histórico da instalação são transferidos em privado.
