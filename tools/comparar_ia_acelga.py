#!/usr/bin/env python3
"""Compara original, fundo mascarado e recorte com o Detector local da API.

Executar no notebook, com a venv tcc-ia. Nao acessa a API, banco ou Raspberry.
Requer o diagnostico completo da delimitacao V3 e a fotografia original.
Nao altera limiares, modelo, arquivos de entrada ou codigo do backend.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

VERSION = "comparacao-ia-escopo-v1"
BACKGROUND = 114


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_image(path, Image, ImageOps):
    raw = path.read_bytes()
    if len(raw) > 30 * 1024 * 1024:
        raise ValueError(f"Arquivo maior que 30 MiB: {path.name}")
    with Image.open(BytesIO(raw)) as source:
        if source.format not in {"JPEG", "PNG"}:
            raise ValueError("Use fotografias JPEG ou PNG")
        if source.width * source.height > 24_000_000:
            raise ValueError("Imagem acima de 24 megapixels")
        image = ImageOps.exif_transpose(source).convert("RGB")
    return raw, image


def prepare_inputs(original, folder, np, Image, ImageOps):
    report = json.loads((folder / "resumo.json").read_text(encoding="utf-8-sig"))
    if report.get("versao") != "escopo-planta-experimental-v3":
        raise ValueError("Selecione a pasta do diagnostico V3")
    if (report.get("status") != "revisao_visual_necessaria"
            or report.get("validacao_geometrica", {}).get("rejeitada") is not False):
        raise ValueError("O diagnostico nao aprovou a geometria; nao executar a IA")
    if report.get("sistema_coordenadas") != (
            "imagem_apos_normalizacao_exif; bbox usa limites x2/y2 exclusivos"):
        raise ValueError("Sistema de coordenadas desconhecido")
    raw, source = read_image(original, Image, ImageOps)
    if sha256(raw) != report.get("sha256_original"):
        raise ValueError("A fotografia nao e a original usada nesse diagnostico (SHA256 diferente)")
    if list(source.size) != report.get("resolucao_imagem"):
        raise ValueError("Resolucao da fotografia difere do diagnostico")
    mask_path = folder / "mascara.png"
    mask_bytes = mask_path.read_bytes()
    with Image.open(BytesIO(mask_bytes)) as mask_file:
        if mask_file.format != "PNG" or mask_file.mode != "L" or mask_file.size != source.size:
            raise ValueError("Mascara deve ser PNG em tons de cinza, nas dimensoes da original")
        values = np.array(mask_file)
    if not np.isin(values, [0, 255]).all() or not (values == 255).any():
        raise ValueError("Mascara vazia ou nao binaria")
    selected = values == 255
    ys, xs = np.nonzero(selected)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    if bbox != report.get("bbox_xyxy_original") or bbox[:2] != report.get("offset_recorte_xy"):
        raise ValueError("Limites da mascara diferem do relatorio")
    if int(selected.sum()) != report.get("pixels_selecionados"):
        raise ValueError("Area da mascara difere do relatorio")
    rgb = np.array(source)
    masked = np.full_like(rgb, BACKGROUND)
    masked[selected] = rgb[selected]
    x1, y1, x2, y2 = bbox
    crop = np.ascontiguousarray(masked[y1:y2, x1:x2])
    crop_bytes, saved_crop = read_image(folder / "recorte-experimental.png", Image, ImageOps)
    if not np.array_equal(np.array(saved_crop), crop):
        raise ValueError("Recorte difere da original com essa mascara; confira a pasta escolhida")
    variants = [
        ("01-original", rgb, [0, 0]),
        ("02-fundo-mascarado", masked, [0, 0]),
        ("03-recorte", crop, bbox[:2]),
    ]
    provenance = {
        "original_sha256": sha256(raw), "mascara_sha256": sha256(mask_bytes),
        "recorte_sha256": sha256(crop_bytes), "delimitacao": report["versao"],
        "marcador": report.get("marcador_alvo"), "bbox_original": bbox,
        "resolucao_original": list(source.size), "fundo_rgb": [BACKGROUND] * 3,
    }
    return variants, provenance


def load_backend(project):
    path = project / "backend" / "app" / "inference.py"
    source_hash = sha256(path.read_bytes())
    spec = importlib.util.spec_from_file_location("tcc_inference_comparacao", path)
    if spec is None or spec.loader is None:
        raise ValueError("Nao foi possivel carregar inference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("Detector", "PIPELINE_VERSION", "CONFIDENCE", "IOU", "SIZE", "MAX_DETECTIONS"):
        if not hasattr(module, name):
            raise ValueError(f"inference.py incompativel: falta {name}")
    detector = module.Detector(project / "artifacts" / "models" / "pulgao-v1.onnx")
    return detector, {
        "inference_sha256": source_hash, "pipeline": module.PIPELINE_VERSION,
        "modelo_sha256": detector.sha256,
        "configuracao_sha256": detector.configuration_sha256,
        "limiar": module.CONFIDENCE, "iou_nms": module.IOU,
        "tamanho_entrada": module.SIZE, "max_deteccoes": module.MAX_DETECTIONS,
    }


def infer_variants(detector, variants, output, Image, ImageDraw, ImageOps):
    results, panels = [], []
    for name, rgb, offset in variants:
        print(f"Analisando {name}...", flush=True)
        detections = detector.predict(rgb)
        annotated = Image.fromarray(rgb)
        draw = ImageDraw.Draw(annotated)
        records = []
        for detection in detections:
            score = float(detection["confidence"])
            box = [float(value) for value in detection["xyxy"]]
            if len(box) != 4 or not all(math.isfinite(v) for v in box + [score]):
                raise ValueError("Detector retornou valores invalidos")
            x1, y1, x2, y2 = box
            height, width = rgb.shape[:2]
            if not (0 <= score <= 1 and 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError("Detector retornou caixa ou pontuacao fora dos limites")
            draw.rectangle(box, outline=(220, 35, 25), width=max(2, round(width / 400)))
            label = f"pulgao {score:.1%}"
            tx, ty = max(0, int(x1)), max(0, int(y1) - 14)
            text_box = draw.textbbox((tx, ty), label)
            draw.rectangle(text_box, fill=(255, 255, 255))
            draw.text((tx, ty), label, fill=(150, 0, 0))
            dx, dy = offset
            records.append({"label": detection["label"], "confidence": score,
                            "xyxy_na_variante": box,
                            "xyxy_na_original": [x1 + dx, y1 + dy, x2 + dx, y2 + dy]})
        filename = f"{name}.png"
        annotated.save(output / filename)
        results.append({"variante": name, "resolucao": [rgb.shape[1], rgb.shape[0]],
                        "offset_original": offset, "quantidade": len(records),
                        "deteccoes": records, "imagem_anotada": filename})
        panel = Image.new("RGB", (640, 550), "white")
        thumb = ImageOps.contain(annotated, (640, 490))
        panel.paste(thumb, ((640 - thumb.width) // 2, 50 + (490 - thumb.height) // 2))
        caption = ImageDraw.Draw(panel)
        caption.text((12, 10), f"{name}: {len(records)} deteccao(oes)", fill="black")
        caption.text((12, 27), f"Entrada: {rgb.shape[1]} x {rgb.shape[0]} px", fill="black")
        panels.append(panel)
        print(f"  {len(records)} deteccao(oes) acima do limiar.", flush=True)
    comparison = Image.new("RGB", (640 * len(panels), 590), "white")
    for index, panel in enumerate(panels):
        comparison.paste(panel, (index * 640, 0))
    draw = ImageDraw.Draw(comparison)
    draw.text((12, 552), "Recorte muda a escala da planta na entrada da IA. Sem filtros adicionais de caixas.", fill="black")
    draw.text((12, 569), "Experimento em uma foto: zero deteccoes nao comprova ausencia de pragas ou validacao do modelo.", fill="black")
    comparison.save(output / "comparacao-ia.png")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--imagem", type=Path, required=True)
    parser.add_argument("--diagnostico", type=Path, required=True)
    parser.add_argument("--projeto", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    image = args.imagem.resolve()
    if not image.is_file() or not args.diagnostico.is_dir():
        raise ValueError("Confira os caminhos de --imagem e --diagnostico")
    output = Path(tempfile.mkdtemp(
        prefix=datetime.now().strftime("comparacao-ia-acelga-%Y%m%d-%H%M%S-"), dir=image.parent))
    report = {"versao": VERSION, "status": "em_execucao",
              "criado_em": datetime.now(timezone.utc).isoformat(),
              "alterou_api": False, "enviou_fotos": False,
              "limites": [
                  "Uma fotografia nao permite estimar precisao ou recall.",
                  "A acelga artificial deste experimento e um caso negativo demonstrativo.",
                  "Pesos e codigo locais so representam a API se suas versoes coincidirem com as do container.",
                  "Mascarar o fundo nao corrige confusao entre a propria planta e pulgoes.",
                  "O recorte muda a escala e o contexto do detector.",
                  "Sem deteccoes significa apenas nenhum resultado acima do limiar nesta entrada.",
                  "Delimitacao experimental; bordas e etiqueta podem remover partes da planta.",
                  "Nenhuma caixa foi descartada por tamanho ou sobreposicao com a mascara.",
              ]}
    print(f"DIAGNOSTICO: {VERSION}\nSaida: {output}", flush=True)
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageOps
        variants, provenance = prepare_inputs(image, args.diagnostico, np, Image, ImageOps)
        report["entradas"] = provenance
        detector, backend_info = load_backend(args.projeto)
        report["backend"] = backend_info
        print(f"Modelo local; limiar {backend_info['limiar']}; IoU {backend_info['iou_nms']}", flush=True)
        report["resultados"] = infer_variants(detector, variants, output, Image, ImageDraw, ImageOps)
        report["status"] = "concluido"
    except KeyboardInterrupt:
        report["status"] = "interrompido"
        report["erro"] = "Execucao interrompida pelo usuario; comparacao incompleta."
        print(report["erro"], file=sys.stderr)
        return 130
    except Exception as exc:
        report["status"] = "falha"
        report["erro"] = f"{type(exc).__name__}: {exc}"
        print(f"ERRO: {report['erro']}", file=sys.stderr)
        return 2
    finally:
        (output / "resumo-ia.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"CONCLUIDO. Envie comparacao-ia.png e resumo-ia.json de: {output}")
    print("Nenhuma foto enviada. API, modelo e motores nao foram alterados.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"INTERROMPIDO: {exc}", file=sys.stderr)
        sys.exit(2)
