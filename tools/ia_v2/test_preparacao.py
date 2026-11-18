"""Verificacao offline com imagens sinteticas; nao mede acuracia do detector."""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image

SCRIPT=Path(__file__).with_name('preparar_praga_v2.py')

def run(*args, ok=True):
    result=subprocess.run([sys.executable,str(SCRIPT),*map(str,args)],capture_output=True,text=True)
    assert (result.returncode==0)==ok, result.stdout+result.stderr
    return result

with tempfile.TemporaryDirectory() as temp:
    p=Path(temp); raw=p/'VOC2007'; base=p/'anterior'; base.mkdir()
    for folder in ['Annotations','JPEGImages','ImageSets/Main']:
        (raw/folder).mkdir(parents=True)
    ids={'trainval':[],'test':[]}; hashes={}; serial=0
    def add(name, labels, split, invalid=False, duplicate=None):
        global serial
        serial+=1
        photo=Image.new('RGB',(100,80),(serial*7%256,serial*11%256,serial*17%256))
        if duplicate:
            photo=Image.open(raw/'JPEGImages'/f'{duplicate}.png').convert('RGB')
        photo.save(raw/'JPEGImages'/f'{name}.png')
        hashes[name]=hashlib.sha256(b'100,80:'+photo.tobytes()).hexdigest()
        bbox='<bndbox><xmin>10</xmin><ymin>20</ymin><xmax>'+('200' if invalid else '30')+'</xmax><ymax>40</ymax></bndbox>'
        objects=''.join(f'<object><name>{label}</name>{bbox}</object>' for label in labels)
        (raw/'Annotations'/f'{name}.xml').write_text(f'<annotation><filename>{name}.png</filename><size><width>100</width><height>80</height></size>{objects}</annotation>')
        ids[split].append(name)
    for label in ('24','37'):
        for n in range(10): add(f'c{label}_{n}',[label],'trainval')
        for n in range(3): add(f't{label}_{n}',[label],'test')
    add('mista',['24','99'],'trainval')
    add('invalida',['37'],'trainval',True)
    add('copia_val',['24'],'trainval',duplicate='c24_0')
    def lists():
        for split,values in ids.items(): (raw/'ImageSets/Main'/f'{split}.txt').write_text('\n'.join(values)+'\n')
    lists()
    old=[{'id':name,'sha256_pixels':hashes[name],'split':s} for name,s in [('c24_0','val'),('c24_1','train'),('t24_0','test')]]
    (base/'manifesto.json').write_text(json.dumps(old))
    raw_before={str(f.relative_to(raw)):hashlib.sha256(f.read_bytes()).hexdigest() for f in raw.rglob('*') if f.is_file()}
    run('auditar',raw,p/'auditoria')
    audit=json.loads((p/'auditoria/resumo.json').read_text())
    assert not audit['modelo_treinado']
    assert all(not name.startswith('t') for samples in audit['amostras_trainval'].values() for name in samples)
    assert (p/'auditoria/rotulo_xml_37.jpg').is_file()
    out=p/'praga'
    run('preparar',raw,out,'--classes-xml','24','37','--base-anterior',base)
    items=json.loads((out/'manifesto.json').read_text())
    by_id={i['id']:i for i in items}
    assert all(by_id[i['id']]['split']==i['split'] for i in old)
    assert len({i['sha256_pixels'] for i in items})==len(items)
    assert not {'mista','invalida','copia_val'}&by_id.keys()
    assert set(i['split'] for i in items)=={'train','val','test'}
    assert '  0: praga' in (out/'data.yaml').read_text()
    assert all(f.read_text().strip()=='0 0.20000000 0.37500000 0.20000000 0.25000000' for f in (out/'labels').rglob('*.txt'))
    original_output=(out/'manifesto.json').read_bytes()
    run('preparar',raw,out,'--classes-xml','24','37',ok=False)
    assert (out/'manifesto.json').read_bytes()==original_output
    run('preparar',raw,p/'faltante','--classes-xml','24','37','--base-anterior',p/'base-inexistente',ok=False)
    run('preparar',raw,p/'classe-inexistente','--classes-xml','24','999',ok=False)
    assert not (p/'classe-inexistente').exists()
    raw_after={str(f.relative_to(raw)):hashlib.sha256(f.read_bytes()).hexdigest() for f in raw.rglob('*') if f.is_file()}
    assert raw_before==raw_after
    add('vazamento',['24'],'test',duplicate='c24_1'); lists()
    run('preparar',raw,p/'conflito','--classes-xml','24','37','--base-anterior',base,ok=False)
    assert not (p/'conflito').exists()
    ids['test'].append('c24_0'); lists()
    run('auditar',raw,p/'overlap',ok=False)
    print('PASSOU: classe unica0, coordenadas normalizadas, splits anteriores, duplicatas, amostras trainval, origem preservada, rejeicao de sobrescrita, base ausente, classe ausente e contaminacao treino/teste.')
