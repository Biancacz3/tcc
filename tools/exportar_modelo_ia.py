r"""Exporta o best.pt local e compara a integracao ONNX com PyTorch em 5 fotos.

Execute usando C:\TCC\venvs\tcc-ia\Scripts\python.exe.
Nao altera best.pt, dataset, banco ou fotografias do painel.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pesos", type=Path, default=Path("C:/TCC/treinos/pulgao-v1/weights/best.pt"))
    parser.add_argument("--amostras", type=Path, default=Path("C:/TCC/datasets/ip102-pulgao-v1/images/val"))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "backend"))
    import numpy as np
    import torch
    from PIL import Image
    from ultralytics import YOLO
    from app.inference import Detector, CONFIDENCE, IOU, PIPELINE_VERSION, preprocess

    if not args.pesos.is_file():
        raise SystemExit(f"Arquivo de pesos nao encontrado: {args.pesos}")
    samples = sorted(p for p in args.amostras.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[:5]
    if len(samples) < 5:
        raise SystemExit("Sao necessarias pelo menos 5 imagens na pasta de validacao.")
    torch.set_num_threads(2)
    with tempfile.TemporaryDirectory(prefix="tcc-export-") as temporary:
        working = Path(temporary) / "best.pt"
        shutil.copyfile(args.pesos, working)
        print("Exportando uma copia do modelo para ONNX FP32...", flush=True)
        exported = Path(YOLO(str(working)).export(format="onnx", imgsz=640, batch=1,
                        dynamic=False, simplify=False, opset=17, nms=False, half=False, device="cpu"))
        detector = Detector(exported)
        original = YOLO(str(args.pesos))
        comparisons = []
        diagnostic_path = project / "diagnostico-exportacao.json"

        def fail(message, diagnostic):
            diagnostic["pipeline"] = PIPELINE_VERSION
            diagnostic["comparacoes_anteriores"] = comparisons
            diagnostic_path.write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")
            raise SystemExit(f"{message} Modelo nao instalado.\nDiagnostico salvo em: {diagnostic_path}")

        for sample in samples:
            with Image.open(sample) as image:
                if image.getexif().get(274, 1) != 1:
                    raise SystemExit(f"Orientacao EXIF nao suportada: {sample.name}")
                rgb = np.array(image.convert("RGB"))
            predicted = detector.predict(rgb)
            reference = original.predict(source=rgb[:, :, ::-1].copy(), device="cpu", imgsz=640, rect=False,
                                         conf=CONFIDENCE, iou=IOU, max_det=300, half=False, verbose=False)[0]
            boxes = reference.boxes.xyxy.cpu().numpy()
            scores = reference.boxes.conf.cpu().numpy()
            tensor = preprocess(rgb)[0]
            tensor_reference = original.predictor.preprocess([rgb[:, :, ::-1].copy()]).cpu().numpy()
            diagnostic = {"imagem": sample.name, "dimensoes": [int(rgb.shape[1]), int(rgb.shape[0])],
                          "diferenca_maxima_entrada": float(np.max(np.abs(tensor-tensor_reference))),
                          "onnx": predicted,
                          "pytorch": [{"xyxy": b.tolist(), "confidence": float(c)} for b,c in zip(boxes,scores)]}
            if diagnostic["diferenca_maxima_entrada"] > 1e-6:
                fail(f"Diferenca no preparo da imagem: {sample.name}.", diagnostic)
            if len(predicted) != len(boxes):
                fail(f"Diferenca na quantidade de deteccoes: {sample.name}.", diagnostic)
            remaining = list(range(len(boxes)))
            max_box_error = max_score_error = 0.0
            for item in predicted:
                distances = [float(np.max(np.abs(boxes[i] - np.array(item["xyxy"])))) for i in remaining]
                nearest = int(np.argmin(distances))
                index = remaining.pop(nearest)
                score_error = abs(float(scores[index])-item["confidence"])
                max_box_error = max(max_box_error, distances[nearest])
                max_score_error = max(max_score_error, score_error)
                if distances[nearest] > 1.0 or score_error > 0.002:
                    diagnostic["diferenca_caixa_pixels"] = distances[nearest]
                    diagnostic["diferenca_pontuacao"] = score_error
                    fail(f"Diferenca em {sample.name}: caixa={distances[nearest]:.6f} pixels, pontuacao={score_error:.6f}.", diagnostic)
            comparisons.append({"imagem": sample.name, "deteccoes": len(predicted), "comparacao": "ok",
                                "erro_max_caixas_pixels": max_box_error, "erro_max_pontuacao": max_score_error})
            print(f"OK: {sample.name} - {len(predicted)} deteccoes equivalentes (caixa: {max_box_error:.6f} px; pontuacao: {max_score_error:.6f})", flush=True)
        destination = project / "artifacts/models"
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "pulgao-v1.onnx"
        staged = destination / "pulgao-v1.onnx.tmp"
        shutil.copyfile(exported, staged)
        os.replace(staged, target)
        report = {"pipeline": PIPELINE_VERSION, "modelo_sha256": detector.sha256,
                  "pesos_pt_sha256": hashlib.sha256(args.pesos.read_bytes()).hexdigest(),
                  "confidence": CONFIDENCE, "iou": IOU, "comparacoes": comparisons,
                  "observacao": "Teste de equivalencia tecnica em 5 fotos; nao substitui avaliacao de acuracia."}
        (destination / "verificacao-exportacao.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"MODELO EXPORTADO E VERIFICADO: {target}", flush=True)


if __name__ == "__main__":
    main()
