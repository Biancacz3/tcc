"""Inferencia local YOLO11n, uma classe, ONNX FP32 640 sem NMS embutido.

O modelo e carregado uma vez por processo; reiniciar a API ao trocar o arquivo.
Preprocessamento RGB letterbox e coordenadas em pixels da imagem original.
"""
import ast
import hashlib
import json
import threading
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

PIPELINE_VERSION = "yolo11-onnx-singleclass-v2"
CONFIDENCE = 0.35
IOU = 0.70
MAX_DETECTIONS = 300
SIZE = 640
MODEL_NAME = "pulgao-v1"


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
            detections.append({"label": "pulgao", "confidence": float(scores[i]),
                               "xyxy": [float(v) for v in box]})
    return detections


class Detector:
    def __init__(self, path):
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
        if ast.literal_eval(meta.get("names", "{}")) != {0: "pulgao"} or meta.get("task") != "detect":
            raise ValueError("O arquivo nao corresponde ao detector de pulgao")
        self.input_name = inputs[0].name
        self.configuration_sha256 = hashlib.sha256(json.dumps({
            "pipeline": PIPELINE_VERSION, "model": self.sha256,
            "confidence": CONFIDENCE, "iou": IOU, "max_det": MAX_DETECTIONS,
        }, sort_keys=True).encode()).hexdigest()

    def predict(self, rgb):
        tensor, ratio, left, top = preprocess(rgb)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode(output, ratio, left, top, rgb.shape[1], rgb.shape[0])


_detector = None
analysis_lock = threading.Lock()


def get_detector():
    # Chamado somente com analysis_lock adquirido.
    global _detector
    if _detector is None:
        _detector = Detector("/app/models/pulgao-v1.onnx")
    return _detector
