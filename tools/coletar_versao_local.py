"""Prepara um ZIP LOCAL para revisão; não publica, roda Docker nem modifica o projeto.

Windows: python coletar_versao_local.py --repo C:\\TCC\\tcc --saida "%USERPROFILE%\\Downloads\\tcc-local.zip"
Raspberry: python3 coletar_versao_local.py --raspberry ~/tcc-raspberry --saida ~/raspberry-local.zip
No PowerShell, use "$env:USERPROFILE" em vez de "%USERPROFILE%". Escolha um nome ainda livre.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from zipfile import ZipFile, ZIP_DEFLATED

MODELO = 'artifacts/models/praga-v2.onnx'
SHA_V2 = '304b45d68d3ba833a0ec8f43c0f3ae47924a1682d1864c9ce26719cc68182083'
LIMIT_BYTES = 2 * 1024**2
MAX_TOTAL = 160 * 1024**2
ARGS = re.I
# Estes caminhos abrangem o código do servidor e os módulos documentados da Raspberry.
DIRS = {'','backend','backend/alembic','backend/alembic/versions','backend/app','backend/app/api',
        'backend/app/db','backend/app/models','backend/app/schemas','backend/app/web','artifacts',
        'artifacts/models','docs','tests','tools','tools/ia_v2','raspberry','raspberry/tests'}
ROOT = {'readme.md','leia-me.md','readme.txt','.gitignore','.gitattributes','compose.yaml','compose.ia.yaml',
        'configurar.ps1','iniciar.ps1','instalar.ps1'}
PI = set(('aproximacao_camera associacao_camera cadencia_camera capture_and_send capture_and_send_integrado '
          'centralizacao_camera config_associacao enquadramento_acelga faixa_camera forca_passos nucleo_robo '
          'partida_prototipo passagem_repetida percurso_cadenciado percurso_repetido pulsos_camera '
          'qualidade_captura registro_robo retorno_camera seguir_linha_v8_horario_lento tcc_integrado visao_robo').split())
PI_TEXT = {'percurso_mesmas_etiquetas_v12.json','percurso_autonomo.json','centralizacao-camera.json','calibracao_linha_v1r.json',
           'tcc-camera.service','tcc-prototipo.service','readme.md','leia-me.md','leia-me-autonomia.md',
           'requirements.txt','.gitignore'}
SKIPNAME = re.compile(r'(?:^|[._-])(?:bak|old|bkp|backup|copia|copy|antigo|log|dump|historico|rascunho|acesso-pessoal|private|credentials?)(?:[._-]|$)|(?:^|[^0-9])20\d{2}[-_]?\d{2}[-_]?\d{2}',ARGS)
PRIVATE = re.compile(
    r'-----BEGIN [^\r\n-]*PRIVATE KEY-----|\b(?:gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{25,}|'
    r'(?:AKIA|ASIA)[A-Z0-9]{16}|sk-[a-zA-Z0-9_-]{24,}|xox[bpars]-[a-z0-9-]{15,})\b|'
    r'\beyJ[a-z0-9_-]{8,}\.eyJ[a-z0-9_-]{5,}\.[a-z0-9_-]{6,}|'
    r'\b[a-z][a-z0-9.+-]*://[^\s/:@{}$]+:[^\s/@{}$]+@|\bBearer[ \t]+[a-z0-9._~+/-]{20,}',ARGS)
SECRET_FILENAME = re.compile(r'(?:^|[._-])(?:secrets?|segredos?|senhas?|passwords?|passwd|tokens?|chaves?|keys?|privad[ao]s?|id_rsa|id_ed25519|credenciais)(?:[._-]|$)',ARGS)
LABEL = r'(?:deviceToken|adminToken|apiToken|jwtSecret|apiKey|password|passwd|senha|(?:\w+_)?(?:api_?key|token|secret(?:_key)?|password|passwd|senha)|chave_privada)'
QUOTED = re.compile(r'''(?ix)(?:\b|["'])('''+LABEL+r''')["']?\s*[:=]\s*["']([^"'\n]{1,})["']''')
PLAIN = re.compile(r'''(?im)^\s*(?:-\s*)?["']?('''+LABEL+r''')["']?\s*[:=]\s*([^\s#,;]+)''')
EMAIL = re.compile(r'\b[\w.%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b',ARGS)

def demonstracao(value):
    return (value.startswith(('$','<','{','os.environ','os.getenv','getpass.','secrets.')) or
            value.lower() in ('', 'none', 'null','str','false','true','...') or
            value.upper().startswith(('SUBSTITUA_', 'EXEMPLO_', 'CHANGE_ME','YOUR_')))

def chaves_restritas(obj):
    if isinstance(obj,dict):
        return any(re.search(r'(?i)(?:token|password|passwd|senha|secret|api.?key|private|credential|ssid|email|endereco_ip|server_url|api_url|wifi)', str(k)) or chaves_restritas(v) for k,v in obj.items())
    return isinstance(obj,list) and any(chaves_restritas(v) for v in obj)

def motivo(texto, nome):
    # Relatórios compartilhados não precisam de identificadores reais dos dispositivos.
    if re.search(r'(?i)(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])', texto):
        return 'possível identificador de dispositivo ou registro; revisar localmente'
    for literal in re.findall(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', texto):
        try:
            endereco = ipaddress.ip_address(literal)
        except ValueError:
            continue
        if not (endereco.is_loopback or endereco.is_unspecified):
            return 'possível endereço de rede; revisar localmente'
    if nome.endswith('.json'):
        try: obj=json.loads(texto)
        except ValueError: return 'JSON de configuração inválido'
        if not isinstance(obj,dict) or chaves_restritas(obj):
            return 'JSON fora do formato público ou com campo reservado'
    if '\0' in texto: return 'texto com bytes nulos'
    if PRIVATE.search(texto): return 'possível chave ou credencial gravada no texto'
    if any(m.group().rsplit('@',1)[1].lower() not in {'example.com','example.org','example.net','example.test','tcc.test'}
           and m.group().lower()!='voce@exemplo.com' and not m.group().lower().endswith(('.invalid','.test')) for m in EMAIL.finditer(texto)):
        return 'possível e-mail pessoal; revisar localmente'
    if any(not demonstracao(m.group(2)) and not (
            m.group(2)=='tcc_dev_' and re.match(r'\s*\+\s*secrets\.token_urlsafe\(',texto[m.end():]))
           for m in QUOTED.finditer(texto)):
        return 'possível credencial literal; revisar localmente'
    # Só tratar atribuições sem aspas como dados em arquivos de configuração.
    if nome.endswith(('.yaml','.yml','.ini','.json','.service')):
        for m in PLAIN.finditer(texto):
            v=m.group(2).strip('\'"')
            if not demonstracao(v) or (v.startswith('${') and re.search(r':-[^$}\s]+',v)
                                      and m.group(1).lower()!='allow_legacy_admin_token'):
                return 'possível credencial literal em configuração'
    return None

def permite(rel, pi):
    seg=rel.lower().split('/'); nome=seg[-1]; d='/'.join(seg[:-1])
    if SKIPNAME.search(nome) or not re.fullmatch(r'[a-z0-9_.-]{1,120}',nome): return False
    if (SECRET_FILENAME.search(nome) and rel.lower() not in {
            'backend/app/passwords.py','backend/alembic/versions/0002_device_tokens.py'}): return False
    if nome.startswith('.env'): return False  # nem exemplos: requerem inspeção manual
    if pi or d=='raspberry' or d=='raspberry/tests':
        pd=d if pi else d.removeprefix('raspberry').lstrip('/')
        if not pd: return (nome.endswith('.py') and nome[:-3] in PI) or nome in PI_TEXT
        return pd=='tests' and bool(re.fullmatch(r'test_[a-z0-9_]+\.py',nome))
    if not d: return nome in ROOT
    if rel.lower()==MODELO: return True
    if d=='docs': return nome.endswith('.md')
    if d=='tools' or d=='tools/ia_v2':
        return bool(re.fullmatch(r'(?:auditar|preparar|enviar|testar|diagnosticar|exportar|conferir|coletar|instalar|adaptar|test)_[a-z0-9_]+\.(?:py|ps1)',nome))
    if d=='tests': return bool(re.fullmatch(r'test_[a-z0-9_]+\.(?:py|cjs|js)',nome))
    if d=='backend': return nome in {'dockerfile','requirements.txt','alembic.ini','.dockerignore'}
    if d=='backend/alembic': return nome in {'env.py','script.py.mako'}
    if d=='backend/alembic/versions': return bool(re.fullmatch(r'\d{4}_[a-z_0-9]+\.py',nome))
    if d=='backend/app/web': return nome in {'index.html','app.js','styles.css'}
    if d.startswith('backend/app') and d in DIRS: return bool(re.fullmatch(r'[a-z_][a-z_0-9]*\.py',nome))
    return False

def linklike(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st,'st_file_attributes',0)&0x400)

def seguro_path(p):
    for q in (p, *p.parents):
        if linklike(q.lstat()): raise ValueError('O caminho passa por link ou junction; use o diretório real.')

def coleta(raiz,pi):
    raiz=Path(raiz).expanduser().absolute()
    seguro_path(raiz)
    if not raiz.is_dir(): raise ValueError('Origem não é uma pasta.')
    arquivos={}; omitidos={}; total=0
    dirs={'','tests'} if pi else DIRS
    for d in sorted(dirs,key=lambda x:(x.count('/'),len(x),x)):
        pasta=raiz/d
        try:
            seguro_path(pasta)
            if not pasta.is_dir(): continue
            entradas=sorted(pasta.iterdir(),key=lambda p:p.name)
        except FileNotFoundError:
            continue
        except (ValueError,OSError):
            omitidos[d or '/']='diretório ilegível ou link; não lido'; continue
        for p in entradas:
            rel=p.relative_to(raiz).as_posix()
            if not permite(rel,pi): continue
            try:
                st=p.lstat()
                if linklike(st) or not stat.S_ISREG(st.st_mode):
                    omitidos[rel]='link, junction ou arquivo não regular'; continue
                if getattr(st,'st_nlink',1)>1:
                    omitidos[rel]='arquivo com hard link'; continue
                model=not pi and rel.lower()==MODELO
                limite=100*1024**2 if model else LIMIT_BYTES
                if st.st_size>limite:
                    omitidos[rel]='tamanho acima do permitido'; continue
                with p.open('rb') as f: data=f.read(limite+1)
                if len(data)>limite:
                    omitidos[rel]='excedeu o limite de tamanho'; continue
                if model:
                    if hashlib.sha256(data).hexdigest()!=SHA_V2:
                        omitidos[rel]='assinatura diferente da V2 confirmada; o modelo não foi incluído'; continue
                else:
                    motivo_arquivo=motivo(data.decode('utf-8-sig'),p.name.lower())
                    if motivo_arquivo: omitidos[rel]=motivo_arquivo; continue
                total+=len(data)
                if total>MAX_TOTAL: raise RuntimeError('Tamanho total inesperado; revise a origem.')
                arquivos[rel]=data
            except (OSError,UnicodeError):
                omitidos[rel]='não legível como arquivo de texto UTF-8 ou binário esperado'
    if not pi and not any(n.lower()==MODELO for n in set(arquivos)|set(omitidos)): omitidos[MODELO]='não encontrado'
    if not ((pi and 'tcc_integrado.py' in arquivos) or
            (not pi and any(n.lower() in {'backend/app/main.py','compose.yaml'} for n in arquivos))):
        raise ValueError('Os arquivos esperados do projeto não foram encontrados ou não puderam ser incluídos.')
    return arquivos,omitidos

def main():
    parser=argparse.ArgumentParser(description='Prepara ZIP local do projeto sem publicar nem mudar o repositório.')
    g=parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--repo',type=Path,help='Pasta do servidor (ex.: C:\\TCC\\tcc)')
    g.add_argument('--raspberry',type=Path,help='Pasta real do robô, diretamente na Raspberry.')
    parser.add_argument('--saida',type=Path,required=True,help='Novo arquivo ZIP fora da pasta de origem.')
    a=parser.parse_args()
    pi=a.raspberry is not None; raiz=(a.raspberry if pi else a.repo).expanduser().absolute()
    saida=a.saida.expanduser().absolute()
    seguro_path(saida.parent)
    if saida.suffix.lower()!='.zip' or saida.resolve().is_relative_to(raiz.resolve()):
        raise ValueError('Escolha um ZIP novo fora da pasta do projeto.')
    arquivos,omitidos=coleta(raiz,pi)
    prefixo='raspberry' if pi else 'repo'
    report={'origem':prefixo,'incluidos':{n:hashlib.sha256(b).hexdigest() for n,b in sorted(arquivos.items())},'omitidos':omitidos,
            'nota':'Lista positiva de fontes. Dados pessoais e segredos não podem ser identificados com certeza. Revise antes de compartilhar.'}
    with saida.open('xb') as f:
        try:
            with ZipFile(f,'w',ZIP_DEFLATED,compresslevel=5) as z:
                for n,b in sorted(arquivos.items()): z.writestr(prefixo+'/'+n,b)
                z.writestr('inventario-publicacao.json',json.dumps(report,ensure_ascii=False,indent=2).encode())
        except BaseException:
            f.close(); saida.unlink(); raise
    print(f'ZIP LOCAL: {saida}\n{len(arquivos)} arquivos incluídos. Nada foi enviado ou instalado.')
    if not pi: print('ONNX confirmado no pacote:', 'SIM' if any(n.lower()==MODELO for n in arquivos) else 'NÃO')
    for n,v in sorted(omitidos.items()): print('Omitido:',n,'-',v)
    print('Revise os arquivos do ZIP antes de compartilhar. Nada fora da lista foi empacotado.')

if __name__=='__main__':
    try: main()
    except (OSError,ValueError,RuntimeError) as e:
        print('Coleta interrompida:',e,file=sys.stderr); sys.exit(1)
