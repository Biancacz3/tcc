"""Inventario e amostras visuais do IP102. Nao altera os arquivos de origem."""
from __future__ import annotations
import argparse
import csv
import json
import random
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origem', type=Path)
    parser.add_argument('saida', type=Path)
    args = parser.parse_args()
    root, out = args.origem.resolve(), args.saida.resolve()
    if root == out or root in out.parents:
        raise SystemExit('Use uma pasta de saida fora do dataset original.')
    annotations, images = root / 'Annotations', root / 'JPEGImages'
    if not annotations.is_dir() or not images.is_dir():
        raise SystemExit('A origem deve conter Annotations e JPEGImages extraidas.')
    out.mkdir(parents=True, exist_ok=True)
    xmls = sorted(annotations.rglob('*.xml'))
    if not xmls:
        raise SystemExit('Nenhuma anotacao XML encontrada.')
    image_files = [p for p in images.rglob('*') if p.is_file() and p.suffix.lower() in {'.jpg','.jpeg','.png'}]
    by_name, by_stem = defaultdict(list), defaultdict(list)
    for path in image_files:
        by_name[path.name.lower()].append(path)
        by_stem[path.stem.lower()].append(path)
    object_counts, image_counts, match_counts = Counter(), Counter(), Counter()
    examples, issues, seen_stems = defaultdict(list), [], set()
    known_ids = set()
    for index, path in enumerate(xmls, 1):
        if index % 5000 == 0:
            print(f'Lidas {index}/{len(xmls)} anotacoes...', flush=True)
        try:
            tree = ET.parse(path).getroot()
            width, height = int(tree.findtext('size/width', '0')), int(tree.findtext('size/height', '0'))
            filename = (tree.findtext('filename') or '').replace('\\','/').split('/')[-1]
            candidates = by_name.get(filename.lower(), [])
            method = 'filename_xml'
            if not candidates:
                candidates = by_stem.get(path.stem.lower(), [])
                method = 'nome_do_xml'
            if len(candidates) != 1:
                issues.append({'xml': path.name, 'erro': 'imagem ausente ou nome ambiguo', 'filename': filename})
                image_path = None
            else:
                image_path = candidates[0]
                match_counts[method] += 1
                known_ids.add(image_path.stem)
            if path.stem in seen_stems:
                issues.append({'xml': path.name, 'erro': 'nome de XML duplicado'})
            seen_stems.add(path.stem)
            known_ids.add(path.stem)
            objects = []
            for obj in tree.findall('object'):
                label = (obj.findtext('name') or '').strip()
                if not label:
                    raise ValueError('Objeto sem classe')
                values = [float(obj.findtext('bndbox/' + key, 'nan')) for key in ('xmin','ymin','xmax','ymax')]
                x1,y1,x2,y2 = values
                valid = width > 0 and height > 0 and 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
                if not valid:
                    issues.append({'xml':path.name,'erro':'caixa ou dimensao invalida','classe':label,'caixa':values})
                object_counts[label] += 1
                objects.append((label, values, valid))
            for label in {obj[0] for obj in objects}:
                image_counts[label] += 1
                if image_path:
                    examples[label].append((image_path, path.name, width, height, objects))
        except (ET.ParseError, OSError, ValueError, TypeError) as error:
            issues.append({'xml': path.name, 'erro': str(error)})
    def sort_key(label):
        return (0, int(label)) if label.isdigit() else (1, label)
    with (out/'classes.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['classe_original','imagens','objetos'])
        for label in sorted(object_counts,key=sort_key):
            writer.writerow([label,image_counts[label],object_counts[label]])
    splits={}
    for path in sorted((root/'ImageSets').rglob('*.txt')):
        lines=[line.split() for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        ids=[Path(parts[0]).stem for parts in lines]
        splits[str(path.relative_to(root))]={
            'linhas':len(lines),'ids_unicos':len(set(ids)),
            'colunas_primeira_linha':len(lines[0]) if lines else 0,
            'primeiras_linhas':[' '.join(parts) for parts in lines[:3]],
            'ids_sem_correspondencia':len(set(ids)-known_ids),
        }
    summary={'xmls':len(xmls),'imagens':len(image_files),'classes':len(object_counts),
             'correspondencia_imagem':dict(match_counts),'quantidade_problemas':len(issues),
             'classes_para_conferencia':{label:{'imagens':image_counts[label],'objetos':object_counts[label]} for label in ['0','24','25','27','29']},
             'listas_originais':splits,
             'observacao':'IDs originais. Nenhuma classe foi confirmada como pulgao, convertida ou selecionada para treino.'}
    (out/'resumo.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    (out/'problemas.json').write_text(json.dumps(issues,indent=2,ensure_ascii=False),encoding='utf-8')
    try:
        font=ImageFont.truetype('DejaVuSans.ttf',16)
    except OSError:
        font=ImageFont.load_default(size=16)
    # Classe 24 e sua vizinha 25 permitem conferir um possivel deslocamento de indice.
    # Os nomes biologicos nao sao inferidos automaticamente a partir desses numeros.
    rng=random.Random(42)
    for label in ['0','24','25','27','29']:
        candidates=examples.get(label,[])
        if not candidates:
            continue
        selected=rng.sample(candidates,min(8,len(candidates)))
        sheet=Image.new('RGB',(1200,80+350*((len(selected)+2)//3)), '#f3f4ee')
        draw=ImageDraw.Draw(sheet)
        draw.text((18,15),f'IP102 | Classe original: {label} | Amostras para conferencia',fill='#17291e',font=font)
        draw.text((18,42),'Caixas verdes = esta classe. Laranjas = outros rotulos. Sem diagnostico automatico.',fill='#17291e',font=font)
        for i,(photo,xml,width,height,objects) in enumerate(selected):
            col,row=i%3,i//3
            x,y=col*400,80+row*350
            draw.text((x+8,y+7),xml[:43],fill='#17291e',font=font)
            try:
                with Image.open(photo) as source:
                    im=source.convert('RGB')
                sx,sy=im.width/max(width,1),im.height/max(height,1)
                if im.size != (width,height):
                    draw.text((x+8,y+30),'AVISO: dimensoes XML diferentes',fill='#b62929',font=font)
                overlay=ImageDraw.Draw(im)
                for current,box,valid in objects:
                    if valid:
                        x1,y1,x2,y2=box
                        overlay.rectangle((x1*sx,y1*sy,x2*sx,y2*sy),outline='#00ff60' if current==label else '#ff9100',width=max(2,im.width//250))
                thumb=ImageOps.contain(im,(384,288))
                sheet.paste(thumb,(x+8+(384-thumb.width)//2,y+53+(288-thumb.height)//2))
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                draw.text((x+8,y+70),'Falha ao abrir a imagem',fill='#b62929',font=font)
                print(f'AVISO: {photo.name}: {error}',flush=True)
        sheet.save(out/f'classe_{label}.jpg',quality=90)
    print('AUDITORIA IP102 CONCLUIDA',flush=True)
    print(json.dumps(summary,indent=2,ensure_ascii=False),flush=True)
    print('Relatorios e amostras salvos na pasta de saida. Originais preservados.',flush=True)

if __name__=='__main__':
    main()
