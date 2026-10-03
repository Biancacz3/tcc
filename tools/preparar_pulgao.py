"""Prepara um subconjunto experimental IP102 classe 24 para deteccao YOLO.

Requer Pillow. Nao altera a origem. Recusa sobrescrever uma saida nao vazia.
Somente imagens com anotacoes exclusivamente da classe 24 sao selecionadas.
Nao constitui um conjunto completo para avaliar ausencia de pulgoes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image


def read_ids(path):
    return {Path(line.split()[0]).stem.lower() for line in
            path.read_text(encoding="utf-8-sig").splitlines() if line.strip()}


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origem", type=Path)
    parser.add_argument("saida", type=Path)
    parser.add_argument("--caminho-treino", required=True,
                        help="Caminho absoluto da saida no computador que treinara o modelo.")
    args = parser.parse_args()
    root, out = args.origem.resolve(), args.saida.resolve()
    if root == out or root in out.parents or out in root.parents:
        raise SystemExit("Origem e saida devem ser pastas separadas, sem sobreposicao.")
    if out.exists() and any(out.iterdir()):
        raise SystemExit("A saida ja possui arquivos. Use uma nova pasta; nada foi sobrescrito.")
    trainval = read_ids(root / "ImageSets/Main/trainval.txt")
    test = read_ids(root / "ImageSets/Main/test.txt")
    if trainval & test:
        raise SystemExit("Ha IDs compartilhados entre trainval e test originais.")
    xmls = sorted((root / "Annotations").rglob("*.xml"))
    if not xmls:
        raise SystemExit("Nenhum XML encontrado. Confira o caminho VOC2007.")
    counts = Counter(p.stem.lower() for p in xmls)
    by_name, by_stem = defaultdict(list), defaultdict(list)
    for path in sorted((root / "JPEGImages").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            by_name[path.name.lower()].append(path)
            by_stem[path.stem.lower()].append(path)
    issues, selected = [], []
    other_classes = 0
    for path in xmls:
        try:
            tree = ET.parse(path).getroot()
            objects = tree.findall("object")
            classes = {(obj.findtext("name") or "").strip() for obj in objects}
            if "24" not in classes:
                other_classes += 1
                continue
            if classes != {"24"}:
                raise ValueError("Imagem com outras classes junto da classe 24; requer revisao")
            key = path.stem.lower()
            if counts[key] != 1:
                raise ValueError("ID de XML duplicado")
            if key not in trainval | test:
                raise ValueError("ID do XML ausente das listas originais")
            filename = (tree.findtext("filename") or "").replace("\\", "/").split("/")[-1]
            named, stemmed = by_name.get(filename.lower(), []), by_stem.get(key, [])
            if named and stemmed and set(named) != set(stemmed):
                raise ValueError("Filename e ID do XML apontam para imagens diferentes")
            candidates = stemmed or named
            if len(candidates) != 1:
                raise ValueError("Imagem ausente ou ambigua")
            photo = candidates[0]
            w = int(tree.findtext("size/width", "0"))
            h = int(tree.findtext("size/height", "0"))
            with Image.open(photo) as source:
                source.load()
                if source.size != (w, h):
                    raise ValueError("Dimensoes da imagem diferentes do XML")
                if source.getexif().get(274, 1) != 1:
                    raise ValueError("Orientacao EXIF precisa de revisao das caixas")
                rgb = source.convert("RGB")
                pixel_hash = hashlib.sha256(f"{w},{h}:".encode() + rgb.tobytes()).hexdigest()
            labels = []
            for obj in objects:
                x1, y1, x2, y2 = [float(obj.findtext("bndbox/" + k, "nan"))
                                  for k in ("xmin", "ymin", "xmax", "ymax")]
                if not (w > 0 and h > 0 and 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
                    raise ValueError("Caixa ou dimensao invalida")
                # Coordenadas xyxy continuas do XML, sem deslocamento ou arredondamento.
                labels.append("0 " + " ".join(f"{v:.8f}" for v in
                              ((x1+x2)/(2*w), (y1+y2)/(2*h), (x2-x1)/w, (y2-y1)/h)))
            selected.append({"id": path.stem, "xml": str(path.relative_to(root)),
                             "imagem": str(photo.relative_to(root)),
                             "original": "test" if key in test else "trainval",
                             "sha256_pixels": pixel_hash,
                             "labels": sorted(set(labels))})
        except (ET.ParseError, OSError, ValueError, TypeError, Image.DecompressionBombError) as exc:
            issues.append({"xml": path.name, "motivo": str(exc)})

    groups = defaultdict(list)
    for item in selected:
        groups[item["sha256_pixels"]].append(item)
    kept = []
    for group in groups.values():
        if len({tuple(item["labels"]) for item in group}) != 1:
            for item in group:
                issues.append({"xml": item["xml"], "motivo": "Pixels identicos com anotacoes divergentes"})
            continue
        # Se uma copia aparece no teste original, nenhuma copia fica em treino/validacao.
        group.sort(key=lambda item: (item["original"] != "test", item["id"]))
        kept.append(group[0])
        for item in group[1:]:
            issues.append({"xml": item["xml"], "motivo": "Pixels duplicados", "mantido": group[0]["id"]})
    pool = sorted((item for item in kept if item["original"] == "trainval"), key=lambda item: item["id"])
    random.Random(42).shuffle(pool)
    nval = max(1, round(len(pool) * 0.2))
    val_ids = {item["id"] for item in pool[:nval]}
    for item in kept:
        item["split"] = "test" if item["original"] == "test" else "val" if item["id"] in val_ids else "train"
    totals = Counter(item["split"] for item in kept)
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "exclusoes.json", issues)
    if any(totals[split] == 0 for split in ("train", "val", "test")):
        raise SystemExit("Uma divisao ficou vazia. Consulte exclusoes.json; dataset nao gerado.")
    for split in ("train", "val", "test"):
        (out / "images" / split).mkdir(parents=True)
        (out / "labels" / split).mkdir(parents=True)
    for item in kept:
        photo = root / item["imagem"]
        shutil.copyfile(photo, out / "images" / item["split"] / (item["id"] + photo.suffix.lower()))
        (out / "labels" / item["split"] / (item["id"] + ".txt")).write_text(
            "\n".join(item["labels"]) + "\n", encoding="utf-8")
    (out / "data.yaml").write_text(
        "path: " + json.dumps(args.caminho_treino.replace("\\", "/")) +
        "\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: pulgao\n", encoding="utf-8")
    summary = {"concluido": True, "classe_xml": "24", "classe_yolo": "0: pulgao",
               "imagens_candidatas_validas": len(selected), "imagens_por_divisao": dict(totals),
               "objetos_por_divisao": {s: sum(len(i["labels"]) for i in kept if i["split"] == s)
                                        for s in ("train", "val", "test")},
               "xmls_sem_classe_24": other_classes, "registros_de_exclusao": len(issues), "seed": 42,
               "teste": "Subconjunto do teste original; nunca usado para criar treino/validacao",
               "validacao": "20% das imagens trainval selecionadas apos deduplicacao",
               "limites": ["Somente positivos da classe 24; nao avalia adequadamente falsos positivos em imagens sem pulgoes",
                           "Deduplicacao por pixels identicos; imagens quase iguais ainda exigem revisao",
                           "Fotografias da Raspberry ainda precisam de avaliacao independente",
                           "Caixas originais mantidas como xyxy continuo; anotacoes difficult incluidas"]}
    save_json(out / "manifesto.json", kept)
    save_json(out / "resumo.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
