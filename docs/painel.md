# Painel de capturas

Primeira interface do TCC, em HTML, CSS e JavaScript, servida pela API FastAPI existente. Não requer Node, npm, uma nova porta ou alterações no Compose. Nenhuma biblioteca externa, fonte ou CDN é carregada pelo painel.

## Instalação no Windows

1. Faça uma cópia de segurança de `backend/app/main.py` fora do repositório.
2. Extraia `tcc-painel.zip` e copie as pastas `backend` e `docs` de dentro do pacote para `C:\TCC\tcc`, mesclando com as existentes. O pacote substitui apenas `backend/app/main.py` e adiciona `backend/app/web/` e este documento. Não substitui o Compose nem as configurações privadas.
3. No PowerShell, em `C:\TCC\tcc`:

```powershell
docker compose up -d --build api
```

4. Abra http://localhost:8000 e entre com o valor de `ADMIN_API_TOKEN` do arquivo `.env`. Cole somente o valor, sem o nome da variável e sem `Bearer`.

O Dockerfile existente deve copiar `app` inteira para `/app/app`, incluindo a nova pasta `web`. A API redireciona `/` para `/painel/`. As rotas `/api/v1`, `/docs` e `/openapi.json` continuam funcionando.

## Uso e limites

- Galeria com até 12 fotos por página, ampliáveis em uma janela com datas, tamanho e identificadores.
- Fotos obtidas exclusivamente pelos endpoints autenticados existentes, com a chave no cabeçalho Authorization. A pasta de fotos não é publicada como conteúdo estático.
- A chave fica só em memória: não é gravada em cookies, localStorage, sessionStorage ou URLs. Recarregar a página exige novo acesso. Sair limpa as imagens carregadas e cancela requisições em andamento.
- Use no computador que executa o sistema, em http://localhost:8000. O acesso HTTP pela rede local não cifra a chave; esta instalação não é uma publicação na internet.
- Os números nos cartões são da página atual, não do banco inteiro. O horário mais recente é o recebimento do primeiro registro da página.
- Os horários usam o fuso do navegador. A data da captura registrada pelo agente corresponde ao início do comando de fotografia.
- Atualização automática a cada 30 segundos, somente na primeira página, com a aba visível e sem foto ampliada. As demais páginas podem ser atualizadas manualmente.
- Paginação por offset segue a API existente. Com novas fotos entrando, registros podem mudar de página. Não é uma exportação consistente de todo o histórico.
- Nomes são buscados no endpoint de dispositivos, até 100. Se não disponíveis, a interface mostra parte do UUID da câmera.
- Em falhas de conexão, mantém os registros anteriores com aviso. Uma falha individual de imagem aparece no próprio cartão e pode ser repetida pelo botão Atualizar.
- Reutiliza as imagens da página já baixadas. Só três downloads acontecem ao mesmo tempo.
- Ainda não há modelo de IA integrado: a interface não informa praga, confiança ou diagnóstico.

## Verificação no ambiente real

1. Entrar com uma chave incorreta deve exibir erro e manter a tela de acesso.
2. Entrar com a chave administrativa deve mostrar as capturas reais da Raspberry.
3. Abrir uma foto deve mostrar imagem, dispositivo, data e resolução.
4. Aguardar um novo envio da Raspberry e a atualização da primeira página.
5. Sair e recarregar: as fotos devem exigir novo acesso.

## Salvar código

Após a verificação:

```powershell
git add backend/app/main.py backend/app/web docs/painel.md
git commit -m "Adiciona painel autenticado para visualizar capturas"
git push
```

O pacote não contém chaves, fotos da plantação, dados de demonstração ou alterações no banco de dados.
