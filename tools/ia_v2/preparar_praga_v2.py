"""Audita IP102/VOC e prepara uma classe YOLO: 0=praga. Nao treina um modelo.

Requer Pillow. Originais e modelos existentes nunca sao alterados. Os numeros
XML devem ser conferidos nas pranchas: a ferramenta nao identifica especies.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

EXTENSIONS = {'.jpg', '.jpeg', '.png'}
CANDIDATES = ['24', '25', '37', '38', '62', '63']
SPLITS = ('train', 'val', 'test')


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read_ids(path):
    values = [Path(s.split()[0].replace('\\', '/')).stem.lower()
              for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
    if len(values) != len(set(values)):
        raise ValueError(f'IDs repetidos na lista: {path}')
    return set(values)


def check_paths(root, out, base=None):
    for source in (root, base):
        if source and (source == out or source in out.parents or out in source.parents):
            raise ValueError('Origem, base anterior e saida devem ser pastas separadas, sem sobreposicao com a saida.')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('Saida ja existe e nao esta vazia. Escolha outra pasta; nada foi sobrescrito.')
    for name in ('Annotations', 'JPEGImages', 'ImageSets/Main'):
        if not (root / name).is_dir():
            raise ValueError(f'Pasta VOC incompleta: falta {name} em {root}')


def inventory(root):
    trainval = read_ids(root / 'ImageSets/Main/trainval.txt')
    test = read_ids(root / 'ImageSets/Main/test.txt')
    if trainval & test:
        raise ValueError('IDs compartilhados entre trainval e test originais.')
    xmls = sorted((root / 'Annotations').rglob('*.xml'))
    if not xmls:
        raise ValueError('Nenhum XML encontrado em Annotations.')
    duplicate_ids = Counter(p.stem.lower() for p in xmls)
    by_name, by_stem = defaultdict(list), defaultdict(list)
    for p in sorted((root / 'JPEGImages').rglob('*')):
        if p.is_file() and p.suffix.lower() in EXTENSIONS:
            by_name[p.name.lower()].append(p)
            by_stem[p.stem.lower()].append(p)
    records, issues = [], []
    for p in xmls:
        try:
            tree = ET.parse(p).getroot()
            objects = tree.findall('object')
            labels = [(o.findtext('name') or '').strip() for o in objects]
            if not objects or any(not label for label in labels):
                raise ValueError('Sem objetos ou objeto sem rotulo XML.')
            key = p.stem.lower()
            if duplicate_ids[key] != 1:
                raise ValueError('ID de XML duplicado.')
            if key not in trainval | test:
                raise ValueError('ID ausente das listas originais trainval/test.')
            filename = (tree.findtext('filename') or '').replace('\\', '/').split('/')[-1]
            named, stemmed = by_name.get(filename.lower(), []), by_stem.get(key, [])
            if named and stemmed and set(named) != set(stemmed):
                raise ValueError('Filename e ID do XML apontam para imagens diferentes.')
            photos = stemmed or named
            if len(photos) != 1:
                raise ValueError('Imagem ausente ou ambigua.')
            w, h = int(tree.findtext('size/width', '0')), int(tree.findtext('size/height', '0'))
            boxes = []
            for obj, label in zip(objects, labels):
                box = [float(obj.findtext('bndbox/' + k, 'nan')) for k in ('xmin', 'ymin', 'xmax', 'ymax')]
                x1, y1, x2, y2 = box
                if not (w > 0 and h > 0 and 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
                    raise ValueError('Caixa ou dimensao invalida.')
                boxes.append({'classe_xml': label, 'xyxy': box})
            records.append({'id': p.stem, 'xml': str(p.relative_to(root)),
                            'imagem': str(photos[0].relative_to(root)),
                            'original': 'test' if key in test else 'trainval',
                            'classes_xml': sorted(set(labels)), 'boxes': boxes, 'size': [w, h]})
        except (ET.ParseError, OSError, ValueError, TypeError) as exc:
            issues.append({'xml': str(p.relative_to(root)), 'motivo': str(exc)})
    return records, issues, len(xmls)


def image_and_hash(root, item):
    with Image.open(root / item['imagem']) as source:
        source.load()
        if source.size != tuple(item['size']):
            raise ValueError('Dimensoes da imagem diferentes do XML.')
        if source.getexif().get(274, 1) != 1:
            raise ValueError('Orientacao EXIF exige revisao das caixas.')
        rgb = source.convert('RGB')
    w, h = rgb.size
    digest = hashlib.sha256(f'{w},{h}:'.encode() + rgb.tobytes()).hexdigest()
    return rgb, digest


def audit(root, out):
    records, issues, total = inventory(root)
    out.mkdir(parents=True, exist_ok=True)
    image_counts, object_counts = Counter(), Counter()
    for item in records:
        image_counts.update(item['classes_xml'])
        object_counts.update(b['classe_xml'] for b in item['boxes'])
    with (out / 'classes.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['rotulo_xml', 'imagens_com_xml_valido', 'objetos_anotados'])
        for label in sorted(image_counts, key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else s)):
            writer.writerow([label, image_counts[label], object_counts[label]])
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 17)
    except OSError:
        font = ImageFont.load_default()
    sampled = {}
    for label in CANDIDATES:
        candidates = [r for r in records if r['original'] == 'trainval' and label in r['classes_xml']]
        random.Random('42:' + label).shuffle(candidates)
        selected = candidates[:8]
        sampled[label] = [r['id'] for r in selected]
        if not selected:
            continue
        sheet = Image.new('RGB', (1200, 120 + 340 * ((len(selected) + 2) // 3)), '#FFFFFF')
        draw = ImageDraw.Draw(sheet)
        draw.text((20, 15), f'Rotulo XML {label} | somente trainval | nao confirma a especie', fill='black', font=font)
        draw.text((20, 45), 'Verde = este rotulo. Laranja = outros. Conferir adulto/larva e insetos da bancada.', fill='black', font=font)
        draw.text((20, 75), 'IDs 24/25/37/38/62/63 sao candidatos para conferencia, nao selecao automatica.', fill='black', font=font)
        for i, item in enumerate(selected):
            x, y = (i % 3) * 400, 120 + (i // 3) * 340
            draw.text((x + 8, y + 7), item['id'][:38], fill='black', font=font)
            try:
                photo, _ = image_and_hash(root, item)
                overlay = ImageDraw.Draw(photo)
                for b in item['boxes']:
                    overlay.rectangle(b['xyxy'], outline='#00B850' if b['classe_xml'] == label else '#FF8C00', width=max(2, photo.width // 200))
                thumb = ImageOps.contain(photo, (384, 288))
                sheet.paste(thumb, (x + 8 + (384 - thumb.width) // 2, y + 38 + (288 - thumb.height) // 2))
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                issues.append({'xml': item['xml'], 'motivo': str(exc)})
                draw.text((x + 8, y + 70), 'Falha na imagem; ver exclusoes.json', fill='red', font=font)
        sheet.save(out / f'rotulo_xml_{label}.jpg', quality=92)
    summary = {'etapa': 'auditoria', 'modelo_treinado': False, 'xmls_lidos': total,
               'xmls_validos': len(records), 'amostras_trainval': sampled,
               'registros_problemas': len(issues),
               'observacao': 'Conferir rotulos e amostras antes de selecionar classes. Teste original nao foi usado nas pranchas.'}
    save_json(out / 'exclusoes.json', issues)
    save_json(out / 'resumo.json', summary)
    return summary


def previous_splits(base):
    by_id, by_hash = {}, defaultdict(set)
    if base is None:
        return by_id, by_hash
    items = json.loads((base / 'manifesto.json').read_text(encoding='utf-8-sig'))
    if not isinstance(items, list) or not items:
        raise ValueError('Manifesto anterior deve ser uma lista nao vazia de imagens.')
    for item in items:
        key, digest, split = str(item['id']).lower(), item['sha256_pixels'], item['split']
        if split not in SPLITS or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Manifesto anterior possui split ou hash invalido.')
        if key in by_id:
            raise ValueError('Manifesto anterior possui ID repetido.')
        by_id[key] = (digest, split)
        by_hash[digest].add(split)
    if any(len(splits) != 1 for splits in by_hash.values()):
        raise ValueError('A base anterior ja possui pixels iguais em divisoes diferentes; exige revisao.')
    return by_id, by_hash


def prepare(root, out, classes, base=None, training_path=None):
    records, issues, total = inventory(root)
    old_ids, old_hashes = previous_splits(base)
    selected = []
    for item in records:
        labels = set(item['classes_xml'])
        if not labels & classes:
            continue
        if not labels <= classes:
            issues.append({'xml': item['xml'], 'motivo': 'Contem outras classes; imagem inteira excluida.', 'classes_xml': item['classes_xml']})
            continue
        try:
            _, item['sha256_pixels'] = image_and_hash(root, item)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            issues.append({'xml': item['xml'], 'motivo': str(exc)})
            continue
        w, h = item['size']
        converted = []
        for b in item['boxes']:
            x1, y1, x2, y2 = b['xyxy']
            # Mantem a convencao xyxy continua utilizada pelo preparador anterior.
            converted.append('0 ' + ' '.join(f'{v:.8f}' for v in ((x1+x2)/(2*w), (y1+y2)/(2*h), (x2-x1)/w, (y2-y1)/h)))
        item['labels'] = sorted(set(converted))
        prior = old_ids.get(item['id'].lower())
        if prior and prior[0] != item['sha256_pixels']:
            raise ValueError(f"Imagem {item['id']} mudou de pixels em relacao a base anterior.")
        locks = set(old_hashes.get(item['sha256_pixels'], set()))
        if prior:
            locks.add(prior[1])
        if item['original'] == 'test':
            locks.add('test')
        if len(locks) > 1:
            raise ValueError(f"Conflito entre divisao anterior e teste original em {item['id']}; nao redistribuido.")
        item['split'] = next(iter(locks), None)
        selected.append(item)
    groups = defaultdict(list)
    for item in selected:
        groups[item['sha256_pixels']].append(item)
    kept = []
    for group in groups.values():
        locks = {r['split'] for r in group if r['split']}
        if len(locks) > 1:
            raise ValueError('Pixels duplicados possuem divisoes anteriores incompatíveis com o teste original.')
        if len({tuple(r['labels']) for r in group}) != 1:
            issues.extend({'xml': r['xml'], 'motivo': 'Pixels identicos com caixas divergentes.'} for r in group)
            continue
        group.sort(key=lambda r: (r['original'] != 'test', r['id'].lower() not in old_ids, r['id']))
        chosen = group[0]
        chosen['split'] = next(iter(locks), None)
        chosen['classes_xml'] = sorted({c for r in group for c in r['classes_xml']})
        kept.append(chosen)
        issues.extend({'xml': r['xml'], 'motivo': 'Pixels duplicados.', 'mantido': chosen['id']} for r in group[1:])
    pools = defaultdict(list)
    for item in kept:
        if item['split'] is None:
            pools[tuple(item['classes_xml'])].append(item)
    for signature, pool in sorted(pools.items()):
        pool.sort(key=lambda r: r['id'])
        random.Random('42:' + ','.join(signature)).shuffle(pool)
        nval = min(len(pool)-1, max(1, round(len(pool)*0.2))) if len(pool) >= 2 else 0
        for i, item in enumerate(pool):
            item['split'] = 'val' if i < nval else 'train'
    totals = Counter(r['split'] for r in kept)
    present = {c for r in kept for c in r['classes_xml']}
    train_present = {c for r in kept if r['split'] == 'train' for c in r['classes_xml']}
    if classes - present or classes - train_present or any(not totals[s] for s in SPLITS):
        raise ValueError(f'Dataset insuficiente: divisões={dict(totals)}; classes ausentes={sorted(classes-present)}; sem treino={sorted(classes-train_present)}. Nada foi gerado.')
    summary = {'etapa': 'preparacao', 'concluido': True, 'modelo_treinado': False,
               'classes_xml': sorted(classes), 'classe_yolo': '0: praga', 'xmls_lidos': total,
               'imagens_por_divisao': dict(totals),
               'objetos_por_divisao': {s: sum(len(r['labels']) for r in kept if r['split'] == s) for s in SPLITS},
               'registros_exclusao': len(issues), 'base_anterior': str(base) if base else None,
               'divisao': 'Teste original preservado; base anterior preservada por ID/pixels. Novos trainval: cerca de 20% para val por assinatura de classes, seed 42.',
               'limites': ['Somente imagens positivas; incluir uma avaliacao independente com negativos.',
                           'Quase duplicatas e caixas originais exigem revisao visual.',
                           'Os rotulos XML nao confirmam especie nem fase de vida.',
                           'Coordenadas xyxy continuas; objetos difficult incluidos.',
                           'Nenhum treinamento, exportacao ou instalacao executado.']}
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.praga-preparo-', dir=out.parent) as temp:
        stage = Path(temp) / 'dataset'
        stage.mkdir()
        for split in SPLITS:
            (stage / 'images' / split).mkdir(parents=True)
            (stage / 'labels' / split).mkdir(parents=True)
        for item in kept:
            photo = root / item['imagem']
            shutil.copyfile(photo, stage / 'images' / item['split'] / (item['id'] + photo.suffix.lower()))
            (stage / 'labels' / item['split'] / (item['id'] + '.txt')).write_text('\n'.join(item['labels']) + '\n', encoding='utf-8')
        yaml_path = (training_path or str(out)).replace('\\', '/')
        (stage / 'data.yaml').write_text('path: ' + json.dumps(yaml_path) + '\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: praga\n', encoding='utf-8')
        save_json(stage / 'manifesto.json', kept)
        save_json(stage / 'resumo.json', summary)
        save_json(stage / 'exclusoes.json', issues)
        if out.exists():
            out.rmdir()  # Apenas a pasta vazia verificada; nunca remove conteudo.
        stage.rename(out)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='acao', required=True)
    for name in ('auditar', 'preparar'):
        command = sub.add_parser(name)
        command.add_argument('origem', type=Path, help='Pasta VOC com Annotations, JPEGImages e ImageSets/Main.')
        command.add_argument('saida', type=Path, help='Nova pasta, fora da origem.')
        if name == 'preparar':
            command.add_argument('--classes-xml', nargs='+', required=True, help='Rotulos EXATOS conferidos no XML; todos viram 0=praga.')
            command.add_argument('--base-anterior', type=Path, help='Dataset anterior com manifesto.json; preserva divisoes existentes.')
            command.add_argument('--caminho-treino', help='Caminho absoluto da nova base no computador de treinamento; padrao=saida.')
    args = parser.parse_args()
    root, out = args.origem.resolve(), args.saida.resolve()
    base = getattr(args, 'base_anterior', None)
    base = base.resolve() if base else None
    try:
        check_paths(root, out, base)
        result = audit(root, out) if args.acao == 'auditar' else prepare(root, out, set(args.classes_xml), base, args.caminho_treino)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        print(f'Arquivos salvos em: {out}', flush=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'ERRO: {exc}\n')


if __name__ == '__main__':
    main()
