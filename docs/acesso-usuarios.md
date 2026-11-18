# Acesso por e-mail e senha

O painel usa contas individuais com os perfis Administrador e Consulta.
Todas as contas acessam o mesmo projeto; não há separação por organização.

## Primeiro acesso

O instalador preserva a conta existente. Quando não há administrador ativo,
solicita e-mail, nome e senha no terminal. Não há senha padrão ou cadastro público.
Entre em http://localhost:8000/painel/ com a conta criada.

## Pelo painel

Administradores usam "Usuários" para criar contas, editar nome/permissão,
ativar/desativar e redefinir senhas. O último administrador ativo é preservado;
para desativar/rebaixar a própria conta é necessário outro administrador.

Todos podem usar "Minha conta" para alterar a própria senha, informando a atual.
Trocar senha ou alterar acesso encerra as sessões afetadas. O usuário precisa
entrar novamente. Reset de outra conta não a reativa automaticamente.

Contas de consulta visualizam capturas, análises, histórico e câmeras.
Administradores também enviam fotos e solicitam novas análises.

## Recuperação local

No PowerShell, em C:\TCC\tcc:

```powershell
docker compose -f compose.yaml -f compose.ia.yaml run --rm --no-deps api python -m app.manage_users reset-password
```

A ferramenta local pede o e-mail, redefine a senha e reativa a conta.
Esse procedimento não envia e-mail e não apaga capturas ou resultados.
As chaves individuais do robô continuam separadas das contas do painel.

## Configuração e controles

- Senhas de 12 a 128 caracteres, scrypt com sal aleatório.
- Cookie HttpOnly e SameSite=Lax; somente o hash da sessão fica no banco.
- Validade absoluta de 7 dias por padrão; SESSION_DAYS aceita 1 a 30.
- Escritas pelo navegador exigem origem correspondente e token CSRF.
- Limite de login: 10 tentativas por conta e 30 por IP em 15 minutos.
- SESSION_COOKIE_SECURE=false para HTTP local; use true com HTTPS.
- APP_ORIGIN pode indicar a origem externa quando há proxy.
- ALLOW_LEGACY_ADMIN_TOKEN=false por padrão; o painel sempre usa sessões.

Os testes de autenticação e administração usam API real com SQLite temporário.
Os bloqueios de concorrência foram implementados para PostgreSQL e precisam
ser verificados nesse banco na instalação.
