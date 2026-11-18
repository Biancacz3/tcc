#!/usr/bin/env python3
r"""TCC: diagnostico experimental da area da planta, sem alterar a API.

Uso no PowerShell (ambiente de IA ja instalado):
C:\TCC\venvs\tcc-ia\Scripts\python.exe .\tools\delimitar_acelga_v3.py C:\TCC\foto-original.jpg --marcador 21

Requer numpy, Pillow e OpenCV >= 4.7 com cv2.aruco. Nao instala dependencias.
O programa nao aciona motores, nao envia fotografias nem executa o YOLO.
Cria uma pasta nova junto da imagem e preserva o arquivo de entrada.
V3: parede acinzentada nao recebe indicacao inicial de planta apenas por HSV.
As candidatas iniciais precisam de predominancia verde e ligacao com a semente.
Pixels restantes na janela continuam candidatos a fundo, nao exclusoes certas.
Mantem o diagnostico V2 e os mesmos criterios de rejeicao.
Uma mascara rejeitada continua rejeitada; nao produz recorte para inferencia.

Metodo: marcador ArUco 4x4_50 seleciona uma janela; sementes de cor inicializam
GrabCut. Isso NAO identifica a especie vegetal. O contrato experimental assume
uma planta por etiqueta, marcador proximo da base, folhas acima da etiqueta e
papel com o formato dos identificadores do TCC. A etiqueta e excluida por uma
margem geometrica aproximada: conferir visualmente se todo o papel saiu.

Cor verde serve para inicializacao, nao como filtro final pixel a pixel. As
partes claras podem continuar na mascara; objetos pequenos de outras cores
podem ainda ser perdidos. Uma foto artificial nao valida plantas/pragas reais.
Sem referencia ou mascara plausivel, informa falha; nunca analisa o fundo como
alternativa nem interpreta falha como ausencia de pulgoes.

Fontes: https://docs.opencv.org/4.x/d8/d83/tutorial_py_grabcut.html
https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

VERSION = "escopo-planta-experimental-v3"
PLANTS = {10: ("A", 1), 11: ("A", 2), 20: ("B", 1), 21: ("B", 2)}
MAX_PIXELS = 24_000_000
MAX_BYTES = 30 * 1024 * 1024
WORK_WIDTH = 1024


class ScopeError(ValueError):
    pass


def select_unique_marker(ids: list[int], expected: int) -> int:
    if expected not in PLANTS:
        raise ScopeError("Identificador fora do mapa do TCC.")
    matches = [i for i, value in enumerate(ids) if value == expected]
    if len(matches) != 1:
        raise ScopeError(f"Esperado um unico marcador {expected}; encontrados {len(matches)}. IDs vistos: {ids}.")
    if any(value in PLANTS and value != expected for value in ids):
        raise ScopeError("Mais de uma planta identificada no quadro. Teste um exemplar por foto.")
    return matches[0]


def select_component(candidates: list[tuple[int, int]], min_area: float) -> int:
    candidates = sorted((area, label) for area, label in candidates if area >= min_area)
    if not candidates:
        raise ScopeError("Nao encontrei sementes suficientes para delimitar a planta acima da etiqueta.")
    if len(candidates) > 1 and candidates[-2][0] >= 0.35 * candidates[-1][0]:
        raise ScopeError("Duas regioes de cor relevantes: delimitacao ambigua. Confira o enquadramento.")
    return candidates[-1][1]


def original_bbox(mask, np):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ScopeError("Mascara vazia; nao e um resultado de ausencia de pragas.")
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def green_dominance(rgb, np):
    """Pista fraca de cor para INICIALIZACAO, nunca mascara final de inferencia.

    Exigir G maior que R e B evita a indicacao inicial positiva da parede
    azulada/cinza observada no diagnostico V2. int16 evita underflow uint8.
    Limiares experimentais: ainda exigem imagens novas e outras iluminacoes.
    """
    values = np.asarray(rgb)
    if values.ndim != 3 or values.shape[2] != 3 or values.dtype != np.uint8:
        raise ScopeError("Pistas de cor exigem imagem RGB uint8 de tres canais.")
    red, green, blue = np.moveaxis(values.astype(np.int16), -1, 0)
    return (green - red >= 5) & (green - blue >= 8)


def connected_candidate(component_labels, seed, np):
    """Seleciona apenas componente fraco que contem toda a semente escolhida."""
    labels, seeds = np.asarray(component_labels), np.asarray(seed, dtype=bool)
    if labels.ndim != 2 or labels.shape != seeds.shape or not seeds.any():
        raise ScopeError("Componentes ou sementes invalidos.")
    seed_labels = np.unique(labels[seeds])
    if len(seed_labels) != 1 or seed_labels[0] == 0:
        raise ScopeError("Semente nao esta ligada a uma unica regiao candidata.")
    return labels == seed_labels[0]


def initialize_grabcut(search, probable, sure_seed, label_paper, np):
    """Rotulos OpenCV: BGD=0, FGD=1, PR_BGD=2, PR_FGD=3.

    Falhar na pista de cor nao torna um pixel fundo certo dentro da janela.
    A exclusao certa e somente fora da janela ou no papel da etiqueta.
    """
    layers = [np.asarray(layer, dtype=bool) for layer in (search, probable, sure_seed, label_paper)]
    search, probable, sure_seed, label_paper = layers
    if search.ndim != 2 or any(layer.shape != search.shape for layer in layers):
        raise ScopeError("Dimensoes invalidas na inicializacao do GrabCut.")
    if (sure_seed & ~probable).any() or (probable & ~search).any():
        raise ScopeError("Sementes fora da regiao candidata ou da janela.")
    initial = np.zeros(search.shape, np.uint8)
    initial[search] = 2
    initial[probable] = 3
    initial[sure_seed] = 1
    initial[label_paper] = 0
    return initial


def assess_mask(mask, edge_of_search, marker_area, np):
    """Verificacoes geometricas; nao avalia acerto botanico ou qualidade visual."""
    mask = np.asarray(mask, dtype=bool)
    edge_of_search = np.asarray(edge_of_search, dtype=bool)
    if mask.ndim != 2 or mask.size == 0 or edge_of_search.shape != mask.shape:
        raise ScopeError("Dimensoes invalidas para verificar a mascara.")
    if not np.isfinite(marker_area) or marker_area <= 0:
        raise ScopeError("Area da referencia invalida.")
    pixels = int(np.count_nonzero(mask))
    contacts = {
        "limite_janela": int(np.count_nonzero(mask & edge_of_search)),
        "borda_superior_imagem": int(np.count_nonzero(mask[0])),
        "borda_inferior_imagem": int(np.count_nonzero(mask[-1])),
        "borda_esquerda_imagem": int(np.count_nonzero(mask[:, 0])),
        "borda_direita_imagem": int(np.count_nonzero(mask[:, -1])),
    }
    reasons = []
    if pixels == 0:
        reasons.append("Mascara vazia; nao significa ausencia de pragas.")
    if contacts["limite_janela"]:
        reasons.append("Mascara encostou no limite da janela de busca. Pode haver fundo incluido ou planta fora da janela.")
    if any(value for name, value in contacts.items() if name != "limite_janela"):
        reasons.append("Mascara encostou na borda da fotografia. Pode haver fundo incluido ou planta cortada.")
    if not 1.5 * marker_area <= pixels <= 30 * marker_area:
        reasons.append("Area segmentada fora da faixa experimental.")
    return {
        "rejeitada": bool(reasons),
        "motivos": reasons,
        "contatos_pixels_na_resolucao_processamento": contacts,
        "area_mascara_pixels_processamento": pixels,
        "area_marcador_pixels_processamento": float(marker_area),
    }


def create_output_directory(source: Path) -> Path:
    prefix = "diagnostico-acelga-" + datetime.now().strftime("%Y%m%d-%H%M%S-")
    return Path(tempfile.mkdtemp(prefix=prefix, dir=source.parent))


def dependencies():
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise ScopeError("Biblioteca ausente. Execute com o Python do ambiente tcc-ia, que ja usa OpenCV, numpy e Pillow.") from exc
    if not hasattr(cv2, "aruco") or not hasattr(cv2.aruco, "ArucoDetector"):
        raise ScopeError("Este OpenCV nao oferece ArucoDetector. Envie a versao exibida por: python -m pip show opencv-python opencv-python-headless")
    cv2.setNumThreads(1)
    cv2.setRNGSeed(42)
    return cv2, np, Image, ImageDraw, ImageOps


def segment(rgb, expected, cv2, np):
    height, width = rgb.shape[:2]
    ratio = min(1.0, WORK_WIDTH / max(width, height))
    work = cv2.resize(rgb, (round(width * ratio), round(height * ratio)), interpolation=cv2.INTER_AREA)
    h, w = work.shape[:2]
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(work, cv2.COLOR_RGB2GRAY))
    ids_list = [] if ids is None else [int(value) for value in ids.flatten()]
    index = select_unique_marker(ids_list, expected)
    quad = corners[index].reshape(4, 2).astype(np.float32)
    marker_area = abs(float(cv2.contourArea(quad)))
    if marker_area < 20 ** 2:
        raise ScopeError("Marcador pequeno demais para este teste. Aproxime a camera sem cortar a planta.")
    if not cv2.isContourConvex(quad):
        raise ScopeError("Geometria do marcador invalida.")
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    to_unit = cv2.getPerspectiveTransform(quad, unit)
    yy, xx = np.mgrid[:h, :w]
    grid = np.stack((xx, yy), axis=-1).astype(np.float32)
    uv = cv2.perspectiveTransform(grid.reshape(-1, 1, 2), to_unit).reshape(h, w, 2)
    if not np.isfinite(uv).all():
        raise ScopeError("Nao foi possivel projetar a referencia da etiqueta.")
    u, v = uv[..., 0], uv[..., 1]
    # Janela ampla e aproximada. NAO e o contorno da planta.
    search = (u > -2.5) & (u < 4.0) & (v > -6.5) & (v < 2.1)
    label_paper = (u >= -0.30) & (u <= 1.30) & (v >= -0.25) & (v <= 1.65)
    hsv = cv2.cvtColor(work, cv2.COLOR_RGB2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    color_hint = green_dominance(work, np)
    green = (hue >= 28) & (hue <= 95) & (saturation >= 50) & (value >= 40) & color_hint
    seed_mask = (green & search & ~label_paper & (v < 0.2)).astype(np.uint8)
    total, components, stats, centers = cv2.connectedComponentsWithStats(seed_mask, connectivity=8)
    candidates = []
    for label in range(1, total):
        cx, cy = centers[label]
        point = cv2.perspectiveTransform(np.array([[[cx, cy]]], dtype=np.float32), to_unit)[0, 0]
        if -1.8 <= point[0] <= 2.8 and -6.0 <= point[1] <= 0:
            candidates.append((int(stats[label, cv2.CC_STAT_AREA]), label))
    chosen = select_component(candidates, min_area=max(50, 0.35 * marker_area))
    seed = (components == chosen).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    sure_seed = cv2.erode(seed, kernel, iterations=1).astype(bool)
    if int(sure_seed.sum()) < 20:
        raise ScopeError("Sementes muito finas para inicializar a separacao.")
    # V2 incluia quase toda a parede acinzentada como provavel planta.
    # V3 usa a pista de cor + conexao com a semente, sem excluir folhas/insetos
    # de outras cores da decisao final do GrabCut.
    pale_candidate = (hue >= 22) & (hue <= 100) & (saturation >= 15) & (value >= 30) & color_hint
    eligible = pale_candidate & search & ~label_paper
    _, weak_components = cv2.connectedComponents(eligible.astype(np.uint8), connectivity=8)
    probable_foreground = connected_candidate(weak_components, seed, np)
    initial = initialize_grabcut(search, probable_foreground, sure_seed, label_paper, np)
    initial_counts = {name: int(np.count_nonzero(initial == label)) for label, name in
                      ((0, "fundo_certo"), (1, "planta_certa_semente"), (2, "fundo_provavel"), (3, "planta_provavel"))}
    bg, fg = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(work, cv2.COLOR_RGB2BGR), initial, None, bg, fg, 5, cv2.GC_INIT_WITH_MASK)
    mask = ((initial == cv2.GC_FGD) | (initial == cv2.GC_PR_FGD)).astype(np.uint8)
    # Mantem so o componente associado as sementes; nunca o maior objeto global.
    _, connected = cv2.connectedComponents(mask, connectivity=8)
    overlap = np.bincount(connected[sure_seed].ravel())
    overlap[0] = 0
    keep = int(overlap.argmax())
    if overlap[keep] < 0.9 * int(sure_seed.sum()):
        raise ScopeError("As sementes ficaram separadas em regioes incompatíveis.")
    mask = (connected == keep).astype(np.uint8)
    # Apenas pequenos buracos internos: nao usa envoltoria convexa.
    count, holes, hole_stats, _ = cv2.connectedComponentsWithStats(1 - mask, connectivity=8)
    hole_limit = max(4, round(marker_area * 0.03))
    for label in range(1, count):
        x, y, bw, bh, area = hole_stats[label]
        if x > 0 and y > 0 and x + bw < w and y + bh < h and area <= hole_limit:
            mask[holes == label] = 1
    # Excluir papel por ultimo para nao inclui-lo ao preencher buracos.
    mask[label_paper] = 0
    edge_of_search = search & ~cv2.erode(search.astype(np.uint8), kernel, iterations=2).astype(bool)
    assessment = assess_mask(mask, edge_of_search, marker_area, np)
    contact_mask = mask.astype(bool) & edge_of_search
    contact_mask[0] |= mask[0].astype(bool)
    contact_mask[-1] |= mask[-1].astype(bool)
    contact_mask[:, 0] |= mask[:, 0].astype(bool)
    contact_mask[:, -1] |= mask[:, -1].astype(bool)
    full_mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
    full_label = cv2.resize(label_paper.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
    full_mask[full_label] = False
    bbox = original_bbox(full_mask, np) if full_mask.any() else None
    metadata = {
        "marcadores_vistos": ids_list,
        "marcador_alvo": expected,
        "campo": PLANTS[expected][0], "acelga": PLANTS[expected][1],
        "resolucao_processamento": [w, h],
        "resolucao_imagem": [width, height],
        "bbox_xyxy_original": bbox,
        "offset_recorte_xy": bbox[:2] if bbox else None,
        "pixels_selecionados": int(full_mask.sum()),
        "fracao_imagem_selecionada": round(float(full_mask.mean()), 6),
        "limite_preenchimento_buraco_pixels_processamento": hole_limit,
        "selecao_botanica": False,
        "execucao_yolo": False,
        "validacao_geometrica": assessment,
        "inicializacao": {
            "criterio_cor": "G-R >= 5 e G-B >= 8, combinados com HSV e componente conectado a semente",
            "limiares_experimentais": True,
            "contagem_rotulos_pixels_processamento": initial_counts,
            "fora_da_pista_de_cor_na_janela": "fundo PROVAVEL; GrabCut pode incluir esses pixels",
        },
    }
    layers = {
        name: cv2.resize(layer.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
        for name, layer in {"janela": search, "etiqueta": label_paper, "sementes": sure_seed, "contatos": contact_mask, "candidata_inicial": probable_foreground}.items()
    }
    return full_mask, metadata, layers


def save_diagnostic(rgb, mask, folder, metadata, layers, cv2, np, Image, ImageDraw):
    rejected = metadata["validacao_geometrica"]["rejeitada"]
    mask_name = "mascara-rejeitada.png" if rejected else "mascara.png"
    files = [mask_name, "comparacao.png", "janela-busca.png", "sementes.png", "exclusao-etiqueta.png", "candidata-inicial.png"]
    Image.fromarray((mask * 255).astype(np.uint8)).save(folder / mask_name)
    for layer_name, filename in (("janela", "janela-busca.png"), ("sementes", "sementes.png"), ("etiqueta", "exclusao-etiqueta.png"), ("candidata_inicial", "candidata-inicial.png")):
        Image.fromarray((layers[layer_name] * 255).astype(np.uint8)).save(folder / filename)
    masked = rgb.copy()
    masked[~mask] = 114
    if not rejected:
        x1, y1, x2, y2 = metadata["bbox_xyxy_original"]
        Image.fromarray(masked[y1:y2, x1:x2]).save(folder / "recorte-experimental.png")
        files.append("recorte-experimental.png")
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    outlined = rgb.copy()
    cv2.drawContours(outlined, contours, -1, (255, 70, 10), max(2, rgb.shape[1] // 700))
    window_contours, _ = cv2.findContours(layers["janela"].astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(outlined, window_contours, -1, (0, 200, 255), max(2, rgb.shape[1] // 700))
    # Ampliacao visual do contato para que apareca na comparacao reduzida.
    contact_display = cv2.dilate(layers["contatos"].astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    outlined[contact_display] = (255, 0, 150)
    panels = [Image.fromarray(rgb), Image.fromarray(outlined), Image.fromarray(masked)]
    panel_w, panel_h = 600, round(rgb.shape[0] * 600 / rgb.shape[1])
    board = Image.new("RGB", (panel_w * 3, panel_h + 135), "white")
    draw = ImageDraw.Draw(board)
    captions = ["1. Imagem original", "2. Laranja: mascara | azul: janela | rosa: contato", "3. MASCARA REJEITADA - somente diagnostico" if rejected else "3. Mascara candidata - requer revisao"]
    for i, (panel, caption) in enumerate(zip(panels, captions)):
        board.paste(panel.resize((panel_w, panel_h)), (panel_w * i, 35))
        draw.text((panel_w * i + 12, 10), caption, fill="black")
    banner = "REJEITADA: esta selecao nao foi aprovada para analise de pragas." if rejected else "PROTOTIPO: requer revisao visual. Nao reconhece pulgoes e nao valida a especie da planta."
    draw.text((12, panel_h + 48), banner, fill=(160, 0, 0) if rejected else "black")
    draw.text((12, panel_h + 70), "Conferir: folhas claras e bordas preservadas; mao, etiqueta, travesseiros e mesa excluidos.", fill="black")
    draw.text((12, panel_h + 92), "A imagem da direita mostra o que o algoritmo selecionou; ela nao prova que a separacao esteja correta.", fill="black")
    board.save(folder / "comparacao.png")
    return files


def run(source: Path, expected: int) -> Path:
    source = source.expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ScopeError("Entrada deve ser uma imagem de ate 30 MiB.")
    cv2, np, Image, ImageDraw, ImageOps = dependencies()
    raw = source.read_bytes()
    with Image.open(BytesIO(raw)) as opened:
        if opened.format not in ("JPEG", "PNG") or opened.width * opened.height > MAX_PIXELS:
            raise ScopeError("Use JPEG ou PNG de ate 24 megapixels.")
        source_size = list(opened.size)
        source_orientation = int(opened.getexif().get(274, 1))
        rgb = np.array(ImageOps.exif_transpose(opened).convert("RGB"))
    folder = create_output_directory(source)
    report = {
        "versao": VERSION, "status": "falha",
        "imagem_origem": source.name,
        "sha256_original": hashlib.sha256(raw).hexdigest(),
        "criado_em": datetime.now(timezone.utc).isoformat(),
        "opencv": cv2.__version__,
        "resolucao_arquivo_original": source_size,
        "orientacao_exif_original": source_orientation,
        "sistema_coordenadas": "imagem_apos_normalizacao_exif; bbox usa limites x2/y2 exclusivos",
        "alterou_api": False, "original_preservado": True,
        "limites": [
            "Prototipo para uma planta com etiqueta proxima da base; nao e segmentacao validada.",
            "V3 ajustada a partir de um caso de parede incorporada; testar novas fotos antes de integrar a API.",
            "Objeto verde ou fundo parecido pode ser selecionado incorretamente, mesmo sem erro do programa.",
            "Papel da etiqueta excluido por aproximacao geometrica; conferir suas bordas.",
            "Detalhes pequenos podem desaparecer na segmentacao; avaliar em fotos novas e com pragas reais.",
            "Delimitar a planta nao corrige a classificacao da propria planta como pulgao.",
        ],
    }
    try:
        mask, metadata, layers = segment(rgb, expected, cv2, np)
        report.update(metadata)
        report["arquivos"] = save_diagnostic(rgb, mask, folder, report, layers, cv2, np, Image, ImageDraw)
        if report["validacao_geometrica"]["rejeitada"]:
            report["status"] = "rejeitado"
            report["erro"] = " ".join(report["validacao_geometrica"]["motivos"])
            print("MASCARA REJEITADA: " + report["erro"])
        else:
            report["status"] = "revisao_visual_necessaria"
            print("DELIMITACAO EXPERIMENTAL GERADA. Ainda requer revisao visual.")
        print("Comparacao para conferir: " + str(folder / "comparacao.png"))
    except (ScopeError, cv2.error, OSError) as exc:
        report["erro"] = str(exc)
        print("NAO FOI POSSIVEL DELIMITAR A PLANTA: " + str(exc))
    finally:
        (folder / "resumo.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Saida: " + str(folder))
        print("Nenhuma foto enviada; API, modelo e motores nao foram alterados.")
    if report["status"] in ("falha", "rejeitado"):
        message = "Envie comparacao.png e resumo.json da pasta indicada." if "comparacao.png" in report.get("arquivos", []) else "Envie resumo.json da pasta indicada."
        raise ScopeError(message)
    return folder


def main():
    parser = argparse.ArgumentParser(description="Teste experimental da area da planta. Nao altera o sistema existente.")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("foto", type=Path, help="Fotografia original, sem caixas desenhadas")
    parser.add_argument("--marcador", type=int, choices=sorted(PLANTS), required=True)
    args = parser.parse_args()
    print("DIAGNOSTICO: " + VERSION)
    try:
        run(args.foto, args.marcador)
    except (ScopeError, OSError, ValueError) as exc:
        print("ERRO: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
