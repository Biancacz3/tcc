"""Inferencia local YOLO11n, uma classe, ONNX FP32 640 sem NMS embutido.

O modelo e carregado uma vez por processo; reiniciar a API ao trocar o arquivo.
Preprocessamento RGB letterbox e coordenadas em pixels da imagem original.
"""
import ast
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from app.plant_regions import RegionConfig, REGION_METHOD, locate_regions

PIPELINE_VERSION = "yolo11-plant-candidate-context-v1"
CONFIDENCE = 0.35
IOU = 0.70
MAX_DETECTIONS = 300
SIZE = 640
MODEL_NAME = 'praga-v2-regioes'


def preprocess(rgb):
    h, w = rgb.shape[:2]
    ratio = min(SIZE / w, SIZE / h)
    nw, nh = round(w * ratio), round(h * ratio)
    left, top = round((SIZE - nw) / 2 - 0.1), round((SIZE - nh) / 2 - 0.1)
    scaled = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    padded = np.full((SIZE, SIZE, 3), 114, dtype=np.uint8)
    padded[top:top+nh, left:left+nw] = scaled
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
    return tensor, ratio, left, top


def decode(output, ratio, left, top, width, height):
    if output.shape != (1, 5, 8400) or not np.isfinite(output).all():
        raise ValueError("Saida do modelo incompativel")
    rows = output[0].T
    rows = rows[(rows[:, 4] >= CONFIDENCE) & (rows[:, 4] <= 1) & (rows[:, 2] > 0) & (rows[:, 3] > 0)]
    if not len(rows):
        return []
    boxes = np.concatenate((rows[:, :2] - rows[:, 2:4] / 2, rows[:, :2] + rows[:, 2:4] / 2), axis=1)
    scores = rows[:, 4]
    order = np.argsort(-scores, kind="stable")
    keep = []
    # NMS em coordenadas do letterbox, antes de limitar as caixas a imagem.
    while len(order) and len(keep) < MAX_DETECTIONS:
        i = int(order[0]); keep.append(i)
        rest = order[1:]
        lo = np.maximum(boxes[i, :2], boxes[rest, :2])
        hi = np.minimum(boxes[i, 2:], boxes[rest, 2:])
        intersection = np.maximum(hi - lo, 0).prod(axis=1)
        area_i = np.prod(boxes[i, 2:] - boxes[i, :2])
        areas = (boxes[rest, 2:] - boxes[rest, :2]).prod(axis=1)
        overlap = intersection / np.maximum(area_i + areas - intersection, 1e-7)
        order = rest[overlap <= IOU]
    detections = []
    # Ultralytics 8.3.203 scale_boxes recalcula o padding usando as dimensoes
    # originais, antes de arredondar o tamanho redimensionado. Esse valor pode
    # diferir em um pixel da borda usada na construcao do letterbox.
    # Reproduzimos o pos-processamento da referencia para manter equivalencia.
    scale_left = round((SIZE - width * ratio) / 2 - 0.1)
    scale_top = round((SIZE - height * ratio) / 2 - 0.1)
    for i in keep:
        box = boxes[i].copy()
        box[[0, 2]] = np.clip((box[[0, 2]] - scale_left) / ratio, 0, width)
        box[[1, 3]] = np.clip((box[[1, 3]] - scale_top) / ratio, 0, height)
        if box[2] > box[0] and box[3] > box[1]:
            detections.append({"label": 'praga', "confidence": float(scores[i]),
                               "xyxy": [float(v) for v in box]})
    return detections


def box_iou(a, b):
    intersection = max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / max(1e-9, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)


def accepted_box(item, candidate, mask, config):
    """Rejeita caixas gigantes, fora da planta ou sem suporte do candidato."""
    box = item.get("xyxy", ())
    score = item.get("confidence", -1)
    if len(box) != 4 or not all(isinstance(v, (int, float)) and np.isfinite(v) for v in box):
        return False
    if not isinstance(score, (int, float)) or not np.isfinite(score) or not CONFIDENCE <= score <= 1:
        return False
    h, w = mask.shape
    x1, y1, x2, y2 = box
    if not 0 <= x1 < x2 <= w or not 0 <= y1 < y2 <= h or item.get("label") != 'praga':
        return False
    if box_iou(box, candidate["xyxy"]) < config.candidate_match_iou:
        return False
    area = (x2 - x1) * (y2 - y1)
    if area > np.count_nonzero(mask) * config.max_detection_plant_fraction:
        return False
    region = mask[int(np.floor(y1)):int(np.ceil(y2)), int(np.floor(x1)):int(np.ceil(x2))]
    if region.size == 0 or np.count_nonzero(region) / region.size < config.min_detection_mask_fraction:
        return False
    return bool(mask[min(h - 1, int((y1 + y2) / 2)), min(w - 1, int((x1 + x2) / 2))])


def merge_detections(items, threshold):
    kept = []
    for item in sorted(items, key=lambda x: -x["confidence"]):
        if all(box_iou(item["xyxy"], other["xyxy"]) <= threshold for other in kept):
            kept.append(item)
    return kept


class Detector:
    def __init__(self, path, config=None):
        self.config = config or RegionConfig()
        model = Path(path).read_bytes()
        self.sha256 = hashlib.sha256(model).hexdigest()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, SIZE, SIZE] or inputs[0].type != "tensor(float)":
            raise ValueError("Exporte ONNX FP32, batch=1 e imgsz=640, dynamic=False")
        if len(outputs) != 1 or outputs[0].shape != [1, 5, 8400]:
            raise ValueError("Esperado detector YOLO11 com uma classe e sem NMS embutido")
        meta = self.session.get_modelmeta().custom_metadata_map
        if ast.literal_eval(meta.get("names", "{}")) != {0: 'praga'} or meta.get("task") != "detect":
            raise ValueError('Esperado um detector de uma classe: 0 = praga.')
        self.input_name = inputs[0].name
        self.configuration_sha256 = hashlib.sha256(json.dumps({
            "pipeline": PIPELINE_VERSION, "model": self.sha256,
            "confidence": CONFIDENCE, "iou": IOU, "max_det": MAX_DETECTIONS,
            "region_method": REGION_METHOD, "region_parameters": asdict(self.config),
        }, sort_keys=True).encode()).hexdigest()

    def _predict_crop(self, rgb):
        tensor, ratio, left, top = preprocess(rgb)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode(output, ratio, left, top, rgb.shape[1], rgb.shape[0])

    def analyze(self, rgb, marker_id=None):
        """Retorna localizacao, deteccoes e estado sem confundir candidato/inseto.

        Positive significa previsao do modelo atual; nao confirmacao biologica.
        A lista de candidatos desconhecidos nunca entra em detection_count.
        """
        started = time.monotonic()
        regions = locate_regions(rgb, marker_id=marker_id, config=self.config)
        candidates = [{"id": item["id"], "xyxy": list(item["xyxy"]), "status": "unidentified"} for item in regions["candidates"]]
        result = {"detections": [], "candidates": candidates, "status": "inconclusive",
                  "warnings": list(regions["warnings"]), "method": PIPELINE_VERSION,
                  "plant_bbox": regions["plant_bbox"], "model_scope": 'praga',
                  "candidate_count": len(candidates), "unidentified_count": len(candidates),
                  "quality": regions.get("quality", {}), "experimental": True}
        if not regions["usable"] or regions["budget_exceeded"] or len(candidates) > self.config.max_candidates:
            return result
        if not candidates:
            result["status"] = "no_detections"
            return result
        h, w = rgb.shape[:2]
        accepted = []
        cache = {}
        for candidate in candidates:
            x1, y1, x2, y2 = candidate["xyxy"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            for scale in self.config.context_scales:
                if time.monotonic() - started > self.config.max_processing_seconds:
                    result["warnings"].append("O prazo de analise terminou antes de avaliar todas as regioes; resultado inconclusivo.")
                    # Nenhuma previsao parcial e publicada como analise completa.
                    return result
                size = min(w, h, self.config.context_max_size, max(self.config.context_min_size, round(max(x2 - x1, y2 - y1) * scale)))
                left = max(0, min(w - size, round(cx - size / 2)))
                top = max(0, min(h - size, round(cy - size / 2)))
                key = (left, top, size)
                if key not in cache:
                    local_boxes = self._predict_crop(rgb[top:top + size, left:left + size])
                    cache[key] = [{**item, "xyxy": [item["xyxy"][0] + left, item["xyxy"][1] + top, item["xyxy"][2] + left, item["xyxy"][3] + top]} for item in local_boxes]
                accepted.extend(item for item in cache[key] if accepted_box(item, candidate, regions["mask"], self.config))
        if time.monotonic() - started > self.config.max_processing_seconds:
            result["warnings"].append("O prazo de analise terminou; as previsoes parciais nao foram publicadas.")
            return result
        result["detections"] = merge_detections(accepted, self.config.dedup_iou)
        for candidate in candidates:
            if any(box_iou(candidate["xyxy"], item["xyxy"]) >= self.config.candidate_match_iou for item in result["detections"]):
                candidate["status"] = "detected"
        result["unidentified_count"] = sum(item["status"] == "unidentified" for item in candidates)
        result["status"] = "positive" if result["detections"] else "review"
        if result["unidentified_count"]:
            result["warnings"].append("Algumas regioes suspeitas nao foram reconhecidas pelo modelo e precisam de revisao.")
        return result

    def predict(self, rgb):
        """Compatibilidade para clientes que precisam somente das deteccoes."""
        return self.analyze(rgb)["detections"]


_detector = None
analysis_lock = threading.Lock()


def get_detector():
    # Chamado somente com analysis_lock adquirido.
    global _detector
    if _detector is None:
        _detector = Detector(os.environ.get("MODEL_PATH", '/app/models/praga-v2.onnx'))
    return _detector
