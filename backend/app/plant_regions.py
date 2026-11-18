"""Localizacao experimental de anomalias na planta do prototipo.

Os parametros foram desenvolvidos em duas fotografias de simulacoes. Esta etapa
gera candidatos, nunca uma confirmacao de inseto. Objetos de cor/tamanho parecidos
podem passar pelos filtros. A mascara pode omitir partes claras ou sombreadas.
"""
from dataclasses import asdict, dataclass
import math
import cv2
import numpy as np

REGION_METHOD = "planta-cor-grabcut-contraste-v1"


@dataclass(frozen=True)
class RegionConfig:
    work_width: int = 1280
    min_image_side: int = 96
    max_image_pixels: int = 20_000_000
    min_seed_fraction: float = .005
    max_seed_fraction: float = .85
    min_laplacian: float = 8.0
    min_brightness: float = 20.0
    max_brightness: float = 245.0
    min_contrast: float = 35.0
    max_dark_ratio: float = .65
    max_dark_value: int = 125
    min_candidate_fraction: float = .00020
    max_candidate_fraction: float = .025
    max_candidate_aspect: float = 6.0
    max_shadow_green: float = .30
    max_candidates: int = 64
    max_raw_contours: int = 5000
    context_scales: tuple[float, ...] = (3.0, 5.0)
    context_min_size: int = 96
    context_max_size: int = 512
    candidate_match_iou: float = .30
    max_detection_plant_fraction: float = .04
    min_detection_mask_fraction: float = .70
    dedup_iou: float = .50
    max_processing_seconds: float = 120.0

    def __post_init__(self):
        values = asdict(self)
        for key, value in values.items():
            seq = value if isinstance(value, tuple) else (value,)
            if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in seq):
                raise ValueError(f"Parametro de regiao invalido: {key}")
        if not 256 <= self.work_width <= 1920 or not 1 <= self.max_candidates <= 128:
            raise ValueError("Resolucao de trabalho ou limite de candidatos invalido")
        for key in ("work_width", "min_image_side", "max_image_pixels", "max_dark_value", "max_candidates", "max_raw_contours", "context_min_size", "context_max_size"):
            if isinstance(values[key], bool) or not isinstance(values[key], int):
                raise ValueError(f"Parametro inteiro esperado: {key}")
        if len(self.context_scales) > 4 or not self.context_scales:
            raise ValueError("Use de uma a quatro escalas de contexto")
        for key in ("min_seed_fraction", "max_seed_fraction", "max_dark_ratio", "min_candidate_fraction", "max_candidate_fraction", "candidate_match_iou", "max_detection_plant_fraction", "min_detection_mask_fraction", "dedup_iou"):
            if not 0 < values[key] <= 1:
                raise ValueError(f"Fracao fora do intervalo: {key}")
        if self.context_min_size > self.context_max_size or self.min_seed_fraction >= self.max_seed_fraction or self.min_candidate_fraction >= self.max_candidate_fraction or self.min_brightness >= self.max_brightness:
            raise ValueError("Limites de regiao inconsistentes")


def validate_rgb(rgb, config):
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Esperada imagem RGB uint8 com tres canais")
    h, w = rgb.shape[:2]
    if min(h, w) < 1 or h * w > config.max_image_pixels:
        raise ValueError("Resolucao fora do intervalo da analise por regioes")


def _fill_holes(mask):
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flooded = padded.copy()
    cv2.floodFill(flooded, None, (0, 0), 255)
    return cv2.bitwise_or(padded, cv2.bitwise_not(flooded))[1:-1, 1:-1]


def _marker_center(rgb, marker_id):
    if marker_id is None:
        return None
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters()).detectMarkers(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    if ids is None:
        return None
    matched = [np.asarray(c).reshape(4, 2).mean(axis=0) for c, number in zip(corners, ids.flatten()) if int(number) == marker_id]
    return matched[0] if len(matched) == 1 else None


def _choose_component(stats, marker, config, size):
    candidates = [i for i in range(1, len(stats)) if stats[i, cv2.CC_STAT_AREA] >= max(100, size * config.min_seed_fraction)]
    if not candidates:
        return None, "Nao foi possivel separar uma planta do fundo."
    largest = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    if stats[largest, cv2.CC_STAT_AREA] > size * config.max_seed_fraction:
        return None, "A regiao verde ocupa quase toda a imagem; enquadramento inconclusivo."
    plausible = [i for i in candidates if stats[i, cv2.CC_STAT_AREA] >= stats[largest, cv2.CC_STAT_AREA] * .20]
    if len(plausible) == 1:
        return largest, None
    if marker is None:
        return None, "Ha mais de uma regiao de planta; a associacao com o identificador precisa ser conferida."
    # Distancia do marcador ao retangulo da planta, nao ao centro da imagem.
    def distance(i):
        x, y, w, h, _ = stats[i]
        return math.hypot(max(x - marker[0], 0, marker[0] - x - w), max(y - marker[1], 0, marker[1] - y - h))
    ranked = sorted((distance(i), i) for i in plausible)
    if ranked[1][0] <= max(8, ranked[0][0] * 1.35):
        return None, "Mais de uma planta esta proxima do marcador; associacao ambigua."
    return ranked[0][1], None


def locate_regions(rgb, marker_id=None, config=None):
    config = config or RegionConfig()
    validate_rgb(rgb, config)
    h, w = rgb.shape[:2]
    if min(h, w) < config.min_image_side:
        return {"candidates": [], "mask": None, "plant_bbox": None,
                "warnings": ["Resolucao insuficiente para avaliar pequenas regioes na planta."],
                "usable": False, "budget_exceeded": False, "method": REGION_METHOD}
    # Limita ambas as dimensoes, inclusive fotografias muito verticais.
    ratio = min(1., config.work_width / w, config.work_width / h)
    im = cv2.resize(rgb, (round(w * ratio), round(h * ratio)), interpolation=cv2.INTER_AREA)
    warnings = ["Metodo experimental para o cenario da demonstracao; partes claras ou sombreadas podem ficar fora da regiao analisada."]
    result = {"candidates": [], "mask": None, "plant_bbox": None, "warnings": warnings, "usable": False, "budget_exceeded": False, "method": REGION_METHOD}
    gray = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
    quality = {"laplacian": float(cv2.Laplacian(gray, cv2.CV_64F).var()), "brightness": float(gray.mean())}
    result["quality"] = quality
    if quality["laplacian"] < config.min_laplacian or not config.min_brightness <= quality["brightness"] <= config.max_brightness:
        warnings.append("A imagem tem pouco detalhe ou iluminacao insuficiente para concluir a analise.")
        return result
    marker = _marker_center(im, marker_id)
    if marker_id is not None and marker is None:
        warnings.append("O identificador informado nao foi localizado de forma unica na foto.")
        return result
    hsv = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)
    r, g, b = [im[:, :, i].astype(np.float32) for i in range(3)]
    excess = 2 * g - r - b
    seed = ((hsv[:, :, 0] >= 28) & (hsv[:, :, 0] <= 95) & (hsv[:, :, 1] >= 30) & (hsv[:, :, 2] >= 35) & (excess > 20)).astype(np.uint8) * 255
    overlay = ((g > 220) & (r < 35) & (b < 35)).astype(np.uint8) * 255
    if np.count_nonzero(overlay) > im.shape[0] + im.shape[1]:
        warnings.append("Foram encontradas marcas verdes muito saturadas; prefira a fotografia original sem desenhos.")
    seed[overlay != 0] = 0
    seed = cv2.morphologyEx(seed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(seed)
    index, error = _choose_component(stats, marker, config, seed.size)
    if error:
        warnings.append(error)
        return result
    x, y, bw, bh, _ = stats[index]
    plant = _fill_holes((labels == index).astype(np.uint8) * 255)
    plant = cv2.morphologyEx(plant, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    gc = np.full(plant.shape, cv2.GC_BGD, np.uint8)
    bx1, bx2 = max(0, x - round(bw * .1)), min(plant.shape[1], x + bw + round(bw * .1))
    by1, by2 = max(0, y - round(bh * .05)), min(plant.shape[0], y + bh + round(bh * .2))
    gc[by1:by2, bx1:bx2] = cv2.GC_PR_BGD
    gc[plant != 0] = cv2.GC_PR_FGD
    sure = cv2.erode(plant, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    gc[sure != 0] = cv2.GC_FGD
    gc[overlay != 0] = cv2.GC_BGD
    if not np.any(gc == cv2.GC_FGD) or not np.any(gc == cv2.GC_BGD):
        warnings.append("Nao ha separacao suficiente entre planta e fundo.")
        return result
    cv2.setRNGSeed(10)
    try:
        cv2.grabCut(im, gc, None, np.zeros((1, 65)), np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        warnings.append("Nao foi possivel delimitar a planta nesta imagem.")
        return result
    plant = _fill_holes(np.isin(gc, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8) * 255)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(plant)
    if count < 2:
        warnings.append("A delimitacao da planta ficou vazia.")
        return result
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    plant = (labels == index).astype(np.uint8) * 255
    x, y, bw, bh, _ = stats[index]
    plant_area = float(np.count_nonzero(plant))
    if not config.min_seed_fraction <= plant_area / plant.size <= config.max_seed_fraction:
        warnings.append("Area delimitada incompativel com uma planta isolada.")
        return result
    if x <= 1 or y <= 1 or x + bw >= plant.shape[1] - 1 or y + bh >= plant.shape[0] - 1:
        warnings.append("A planta pode estar cortada em uma borda da fotografia.")
    inner = cv2.erode(plant, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    distance = cv2.distanceTransform(plant, cv2.DIST_L2, 5)
    kernel = max(21, int(round(bh * .12)) | 1)
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))
    contrast = background.astype(np.float32) - gray
    dark = ((contrast >= config.min_contrast) & (gray <= background * config.max_dark_ratio) & (gray < config.max_dark_value) & (inner != 0) & (overlay == 0)).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    result["plant_bbox"] = [round(x / ratio), round(y / ratio), round((x + bw) / ratio), round((y + bh) / ratio)]
    result["mask"] = cv2.resize(plant, (w, h), interpolation=cv2.INTER_NEAREST)
    if len(contours) > config.max_raw_contours:
        result["budget_exceeded"] = True
        warnings.append("Excesso de detalhes na imagem; a analise ficou inconclusiva.")
        return result
    boxes = []
    for contour in contours:
        xx, yy, ww, hh = cv2.boundingRect(contour)
        area = float(cv2.contourArea(contour))
        if area < max(20, plant_area * config.min_candidate_fraction) or area > plant_area * config.max_candidate_fraction or min(ww, hh) < 4 or max(ww, hh) / min(ww, hh) > config.max_candidate_aspect:
            continue
        region = gray[yy:yy + hh, xx:xx + ww]
        low = region < np.percentile(region, 35)
        relative_green = excess[yy:yy + hh, xx:xx + ww] / (r[yy:yy + hh, xx:xx + ww] + g[yy:yy + hh, xx:xx + ww] + b[yy:yy + hh, xx:xx + ww] + 1)
        green_dark = float(np.median(relative_green[low])) if low.any() else 1.
        if green_dark > config.max_shadow_green or distance[yy + hh // 2, xx + ww // 2] < max(6, min(ww, hh) * .35):
            continue
        boxes.append({"xyxy": [round(xx / ratio), round(yy / ratio), min(w, round((xx + ww) / ratio)), min(h, round((yy + hh) / ratio))]})
    boxes.sort(key=lambda item: (item["xyxy"][1], item["xyxy"][0]))
    result["total_candidates"] = len(boxes)
    if len(boxes) > config.max_candidates:
        result["budget_exceeded"] = True
        warnings.append("Ha mais regioes suspeitas que o limite desta analise; resultado inconclusivo.")
    result["candidates"] = [{"id": i + 1, **item, "status": "unidentified"} for i, item in enumerate(boxes[:config.max_candidates])]
    result["usable"] = not result["budget_exceeded"]
    return result
