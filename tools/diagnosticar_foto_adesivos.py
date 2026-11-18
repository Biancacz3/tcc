#!/usr/bin/env python3
r"""Diagnostico local da foto da Raspberry com o ONNX instalado no notebook.

Exemplo (PowerShell, na pasta C:\TCC\tcc, em uma unica linha):
  C:\TCC\venvs\tcc-ia\Scripts\python.exe .\tools\diagnosticar_foto_adesivos.py C:\TCC\diagnostico-integrado.jpg --modelo C:\TCC\tcc\artifacts\models\pulgao-v1.onnx

Requer Pillow, NumPy, OpenCV e onnxruntime no Python escolhido. Nao chama a API,
nao altera o modelo, foto, banco de dados, limiar implantado ou carrinho. Os
recortes usam a cor verde do cenario artificial e exigem conferencia visual.
Adesivos nao sao pulgoes reais: caixas sao apenas previsoes do modelo atual.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path


def remap_detections(detections, offset_x, offset_y):
    """Converte caixas do recorte para coordenadas da fotografia original."""
    return [
        {**item, "xyxy": [item["xyxy"][0] + offset_x,
                            item["xyxy"][1] + offset_y,
                            item["xyxy"][2] + offset_x,
                            item["xyxy"][3] + offset_y]}
        for item in detections
    ]


def find_plant_crop(rgb):
    """Heuristica auditavel para a acelga verde do prototipo; nunca segmenta a praga."""
    import cv2
    import numpy as np

    height, width = rgb.shape[:2]
    ratio = min(1.0, 640 / width, 480 / height)
    small = cv2.resize(rgb, (round(width * ratio), round(height * ratio)),
                       interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([30, 48, 34], np.uint8),
                      np.array([95, 255, 255], np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None, "Nenhuma area verde conectada encontrada."
    candidate = max(stats[1:], key=lambda row: int(row[cv2.CC_STAT_AREA]))
    x, y, w, h, area = (int(value) for value in candidate)
    if (w < 65 or h < 55 or area < 1200 or
            area > small.shape[0] * small.shape[1] * .40):
        return None, "Area verde dominante incompativel com a planta do cenario."
    # A parte inferior clara nao entra na mascara verde. A reserva inferior
    # deixa a folha e a base visiveis, mas nao substitui uma mascara de planta.
    left = max(0, int((x - .22 * w) / ratio))
    top = max(0, int((y - .10 * h) / ratio))
    right = min(width, round((x + 1.22 * w) / ratio))
    bottom = min(height, round((y + 2.60 * h) / ratio))
    if right - left < 100 or bottom - top < 100:
        return None, "Recorte resultante pequeno demais."
    return [left, top, right, bottom], None


def make_regions(rgb, plant_box):
    """Recorte inteiro e duas metades com sobreposicao; limites xyxy exclusivos."""
    x1, y1, x2, y2 = plant_box
    mid = (y1 + y2) // 2
    overlap = max(1, round((y2 - y1) * .12))
    boxes = {
        "planta": [x1, y1, x2, y2],
        "superior": [x1, y1, x2, min(y2, mid + overlap)],
        "inferior": [x1, max(y1, mid - overlap), x2, y2],
    }
    return {name: {"box": box, "rgb": rgb[box[1]:box[3], box[0]:box[2]]}
            for name, box in boxes.items()}


def _analyze(detector, rgb):
    """Uma passagem ONNX: mesmo preprocess/decode do backend, com contagens cruas."""
    import numpy as np
    from app.inference import CONFIDENCE, decode, preprocess

    tensor, ratio, left, top = preprocess(rgb)
    output = detector.session.run(None, {detector.input_name: tensor})[0]
    # decode tambem confere forma (1,5,8400), valores finitos e executa NMS.
    detections = decode(output, ratio, left, top, rgb.shape[1], rgb.shape[0])
    scores = output[0, 4]
    candidate_counts = {f"{threshold:.2f}": int(np.count_nonzero(scores >= threshold))
                        for threshold in (0.05, 0.10, 0.20, CONFIDENCE)}
    below = {f"{threshold:.2f}_to_{CONFIDENCE:.2f}":
             int(np.count_nonzero((scores >= threshold) & (scores < CONFIDENCE)))
             for threshold in (0.05, 0.10, 0.20)}
    return {"detections": detections, "detection_count": len(detections),
            "raw_candidate_counts_before_nms": candidate_counts,
            "raw_candidate_counts_below_deployed_threshold": below,
            "max_raw_score": float(scores.max()) if len(scores) else None,
            "note": "Contagens cruas exploratorias; nao sao pulgoes confirmados."}


def _panel(rgb, detections, title, *, crop_outline=None, size=(900, 520)):
    import cv2
    import numpy as np

    source = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    if crop_outline is not None:
        x1, y1, x2, y2 = crop_outline
        cv2.rectangle(source, (x1, y1), (x2 - 1, y2 - 1), (255, 255, 0), 4)
    for item in detections:
        x1, y1, x2, y2 = (int(round(value)) for value in item["xyxy"])
        cv2.rectangle(source, (x1, y1), (x2, y2), (0, 210, 255), 3)
        cv2.putText(source, f"modelo {item['confidence']:.2f}",
                    (x1, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, .65,
                    (0, 210, 255), 2, cv2.LINE_AA)
    panel_w, panel_h = size
    output = np.full((panel_h + 44, panel_w, 3), 245, dtype=np.uint8)
    ratio = min(panel_w / source.shape[1], panel_h / source.shape[0])
    new_w, new_h = round(source.shape[1] * ratio), round(source.shape[0] * ratio)
    scaled = cv2.resize(source, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x, y = (panel_w - new_w) // 2, 44 + (panel_h - new_h) // 2
    output[y:y + new_h, x:x + new_w] = scaled
    cv2.putText(output, title, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, .7,
                (20, 20, 20), 2, cv2.LINE_AA)
    return output


def write_comparison(rgb, analyses, regions, plant_box, target):
    import cv2
    import numpy as np

    full = _panel(rgb, analyses["foto_inteira"]["detections"],
                  f"Foto inteira: {analyses['foto_inteira']['detection_count']} caixa(s)",
                  crop_outline=plant_box)
    rendered = [full]
    for name, title in (("planta", "Recorte da planta"),
                        ("superior", "Parte superior"),
                        ("inferior", "Parte inferior")):
        region = regions.get(name)
        if region is None:
            blank = np.full_like(full, 245)
            cv2.putText(blank, "Recorte nao localizado", (40, 280),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (35, 35, 35), 2)
            rendered.append(blank)
        else:
            item = analyses[name]
            rendered.append(_panel(region["rgb"], item["detections"],
                                   f"{title}: {item['detection_count']} caixa(s)"))
    comparison = np.vstack((np.hstack(rendered[:2]), np.hstack(rendered[2:])))
    if not cv2.imwrite(str(target), comparison, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise OSError("Nao foi possivel salvar a comparacao")


def diagnose(photo_path, model_path, output_dir=None):
    import numpy as np
    from PIL import Image

    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "backend"))
    from app.inference import CONFIDENCE, IOU, PIPELINE_VERSION, Detector

    photo_path, model_path = Path(photo_path), Path(model_path)
    if not photo_path.is_file() or not model_path.is_file():
        raise ValueError("Foto ou modelo ONNX nao encontrado.")
    data = photo_path.read_bytes()
    if len(data) > 10 * 1024 * 1024 or not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("Esperado JPEG original de ate 10 MiB.")
    with Image.open(photo_path) as photo:
        if photo.format != "JPEG" or photo.getexif().get(274, 1) != 1:
            raise ValueError("Use JPEG sem rotacao EXIF, como a foto enviada pela Raspberry.")
        if photo.width * photo.height > 20_000_000:
            raise ValueError("Imagem acima de 20 milhoes de pixels.")
        rgb = np.array(photo.convert("RGB"))
    detector = Detector(model_path)
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="diagnostico-adesivos-", dir=photo_path.parent))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=False)
    crop, reason = find_plant_crop(rgb)
    regions = make_regions(rgb, crop) if crop else {}
    analyses = {"foto_inteira": _analyze(detector, rgb)}
    for name, region in regions.items():
        result = _analyze(detector, region["rgb"])
        result["crop_original_xyxy"] = region["box"]
        result["detections_original_xyxy"] = remap_detections(result["detections"],
                                                               region["box"][0], region["box"][1])
        analyses[name] = result
    report = {
        "kind": "diagnostico_offline_adesivos_v1", "source_image": photo_path.name,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_size": [int(rgb.shape[1]), int(rgb.shape[0])],
        "model_sha256": detector.sha256, "pipeline": PIPELINE_VERSION,
        "deployed_confidence": CONFIDENCE, "deployed_iou": IOU,
        "plant_crop_method": "componente_verde_dominante_reserva_parte_clara",
        "plant_crop_original_xyxy": crop, "plant_crop_warning": reason,
        "analyses": analyses,
        "limits": ["Adesivos simulam insetos, nao validam deteccao de pulgoes reais.",
                   "Recorte por cor pode incluir fundo ou cortar a parte clara: confira comparacao.jpg.",
                   "Pontuacoes baixas e caixas sao exploratorias; nao alteram o limiar da API.",
                   "Caixas do recorte podem se repetir nas duas metades; nao some as contagens."],
        "untouched": ["foto_original", "modelo", "API", "banco", "Raspberry", "motores"],
    }
    report_path = output_dir / "resumo.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_comparison(rgb, analyses, regions, crop, output_dir / "comparacao.jpg")
    return output_dir, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("foto", type=Path, help="JPEG original da Raspberry, nao screenshot do painel")
    parser.add_argument("--modelo", type=Path,
                        default=Path("C:/TCC/tcc/artifacts/models/pulgao-v1.onnx"))
    parser.add_argument("--saida", type=Path, help="Nova pasta para resumo.json e comparacao.jpg")
    args = parser.parse_args()
    try:
        output, report = diagnose(args.foto, args.modelo, args.saida)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"ERRO: {exc}\n")
    print(f"DIAGNOSTICO_SALVO: {output}")
    for name, item in report["analyses"].items():
        print(f"{name}: caixas com limiar {report['deployed_confidence']:.2f} = "
              f"{item['detection_count']}; maior pontuacao bruta = "
              f"{item['max_raw_score']:.4f}; candidatas brutas >=0.10 = "
              f"{item['raw_candidate_counts_before_nms']['0.10']}")
    if report["plant_crop_warning"]:
        print("RECORTE_NAO_VALIDADO: " + report["plant_crop_warning"])
    print("Foto e modelo preservados; nenhuma captura ou analise foi enviada a API.")


if __name__ == "__main__":
    main()
