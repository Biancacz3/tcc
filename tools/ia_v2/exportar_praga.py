"""Exporta a classe praga para ONNX e compara PyTorch e ONNX em cinco fotos.

O destino e um arquivo .onnx NOVO em uma pasta de preparacao.
O resultado e a equivalencia dos pesos; o teste nao mede qualidade na bancada.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path, PureWindowsPath

SIZE = 640
EXPECTED = (1, 5, 8400)


EXTENSOES = {".jpg", ".jpeg", ".png"}


def _espacadas(items):
    """Comeca com cinco pontos do conjunto ordenado; ha reservas para arquivos inacessiveis."""
    if len(items) < 5:
        return iter(items)
    first = [i * (len(items) - 1) // 4 for i in range(5)]
    return (items[i] for i in (*first, *(i for i in range(len(items)) if i not in first)))


def _ler_foto(path, digest=None):
    from PIL import Image

    with Image.open(path) as im:
        im.load()
        if digest is not None:
            # Mesmo calculo empregado por preparar_praga_v2.py ao montar o manifesto.
            with im.convert("RGB") as rgb:
                h = hashlib.sha256(f"{rgb.width},{rgb.height}:".encode())
                h.update(rgb.tobytes())
            if h.hexdigest() != digest:
                raise ValueError("pixels diferentes dos registrados no manifesto")


def _registros_val(entries):
    if not isinstance(entries, list):
        raise ValueError("manifesto.json: esperada uma lista de imagens.")
    rows = []
    for e in entries:
        if not isinstance(e, dict) or e.get("split") not in ("train", "test", "val"):
            raise ValueError("manifesto.json: registro sem divisao reconhecida.")
        if e["split"] != "val":
            continue
        ident, relative, digest = e.get("id"), e.get("imagem"), e.get("sha256_pixels")
        if not isinstance(ident, str) or not ident or ident in (".", "..") or PureWindowsPath(ident).name != ident:
            raise ValueError("manifesto.json: ID de imagem invalido.")
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ValueError(f"manifesto.json: faltam imagem ou sha256_pixels para {ident}.")
        p = PureWindowsPath(relative)
        if (p.anchor or len(p.parts) < 2 or p.parts[0].lower() != "jpegimages" or ".." in p.parts
                or p.suffix.lower() not in EXTENSOES or any(":" in part for part in p.parts)):
            raise ValueError(f"manifesto.json: caminho da imagem invalido para {ident}: {relative}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"manifesto.json: sha256_pixels invalido para {ident}.")
        rows.append((ident, Path(*p.parts[1:]), digest))
    return sorted(rows, key=lambda row: row[0].casefold())


def _amostras(dataset, origem=None):
    """Seleciona cinco fotos de val. Confere o conteudo da copia original antes de usa-la."""
    from PIL import Image

    root = Path(dataset).absolute()
    if root.name.lower() == "data.yaml":
        if not root.is_file():
            raise ValueError(f"data.yaml nao encontrado: {root}")
        root = root.parent
    if root.name.lower() == "val" and root.parent.name.lower() == "images":
        root = root.parent.parent
    folder, manifest = root / "images" / "val", root / "manifesto.json"
    errors = (OSError, ValueError, Image.DecompressionBombError)
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, PermissionError) as exc:
        # Compatibilidade: uma pasta val legivel continua funcionando mesmo sem manifesto.
        # Nesse modo nunca procurar fotos no backup, que contem tambem treino e teste.
        try:
            images = sorted((p for p in folder.iterdir() if p.suffix.lower() in EXTENSOES),
                            key=lambda p: p.name.casefold())
        except OSError as err:
            raise ValueError(f"Nao consegui ler {manifest} ({exc}) nem listar {folder} ({err}). "
                             "Indique em --dataset uma copia legivel da base V2 com manifesto.json.") from err
        samples, last = [], None
        for path in _espacadas(images):
            try:
                _ler_foto(path)
            except errors as err:
                last = f"{path}: {err}"
                continue
            samples.append(path)
            if len(samples) == 5:
                print(f"5 fotos de validacao lidas em {folder}.", flush=True)
                return samples
        raise ValueError(f"Somente {len(samples)} fotos de validacao legiveis em {folder}. "
                         f"Manifesto indisponivel ({exc}); nao posso conferir fotos do backup. "
                         f"Ultima falha de leitura: {last or 'nenhuma'}.")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Falha ao interpretar {manifest}: {exc}") from exc

    rows = _registros_val(entries)
    if len(rows) < 5:
        raise ValueError(f"{manifest} lista apenas {len(rows)} fotos com split=val; sao necessarias cinco.")
    originals = None
    if origem is not None:
        voc = Path(origem)
        originals = voc if voc.name.lower() == "jpegimages" else voc / "JPEGImages"
    samples, seen, backup_count, last = [], set(), 0, None
    for ident, relative, digest in _espacadas(rows):
        if digest in seen:
            continue
        local = folder / (ident + relative.suffix.lower())
        candidates = [(False, local)]
        if originals is not None:
            candidates.append((True, originals / relative))
        for backup, path in candidates:
            try:
                _ler_foto(path, digest)
            except errors as err:
                last = f"{path}: {err}"
                continue
            samples.append(path)
            seen.add(digest)
            backup_count += int(backup)
            break
        if len(samples) == 5:
            print(f"5 fotos de validacao conferidas pelos pixels do manifesto: {5-backup_count} em {folder}"
                  + (f"; {backup_count} no backup {originals}." if backup_count else "."), flush=True)
            return samples
    fontes = str(folder) if originals is None else f"{folder} e {originals}"
    raise ValueError(f"Somente {len(samples)} das cinco fotos puderam ser conferidas em {fontes}. "
                     "Para buscar originais, indique --origem com a pasta VOC2007 ou JPEGImages. "
                     f"Ultima falha: {last or 'menos de cinco fotos distintas'}.")


def _entrada(path, cv2, np, Image, ImageOps):
    # Preparo RGB letterbox do servidor. O MESMO tensor alimenta ambos os modelos.
    with Image.open(path) as im:
        rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    h, w = rgb.shape[:2]
    ratio = min(SIZE/w, SIZE/h)
    nw, nh = max(1, round(w*ratio)), max(1, round(h*ratio))
    left, top = round((SIZE-nw)/2-0.1), round((SIZE-nh)/2-0.1)
    padded = np.full((SIZE, SIZE, 3), 114, dtype=np.uint8)
    padded[top:top+nh, left:left+nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)/255.0


def _comparar(ref, out, np):
    if ref.shape != EXPECTED or out.shape != EXPECTED or ref.dtype != np.float32 or out.dtype != np.float32:
        raise ValueError(f"Saida incorreta: PyTorch={ref.shape}, {ref.dtype}; ONNX={out.shape}, {out.dtype}.")
    if not np.isfinite(ref).all() or not np.isfinite(out).all():
        raise ValueError("Saida com numero nao finito.")
    if np.min(out[:, 4, :]) < 0 or np.max(out[:, 4, :]) > 1:
        raise ValueError("Pontuacao ONNX fora de [0,1].")
    # Do centro/largura/altura para os quatro cantos no quadro de entrada.
    def cantos(t):
        return np.concatenate((t[:, :2, :]-t[:, 2:4, :]/2, t[:, :2, :]+t[:, 2:4, :]/2), axis=1)
    caixa = float(np.max(np.abs(cantos(ref)-cantos(out))))
    score = float(np.max(np.abs(ref[:, 4, :]-out[:, 4, :])))
    if caixa > 1.0 or score > 0.002:
        raise ValueError(f"PyTorch e ONNX divergem: caixa={caixa:.6f} px no quadro 640; pontuacao={score:.6f}.")
    return {"erro_max_caixas_entrada_px": caixa, "erro_max_pontuacao": score}


def _checar_onnx(model_path, onnx, ort):
    onnx.checker.check_model(str(model_path))
    graph = onnx.load(str(model_path), load_external_data=False)
    if any(t.data_location == onnx.TensorProto.EXTERNAL for t in graph.graph.initializer):
        raise ValueError("O ONNX depende de dados externos: e necessario um unico arquivo.")
    if not any(o.domain in ("", "ai.onnx") and o.version == 17 for o in graph.opset_import):
        raise ValueError("O ONNX nao foi exportado no opset 17.")
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    ins, outs = session.get_inputs(), session.get_outputs()
    if len(ins) != 1 or ins[0].shape != [1, 3, SIZE, SIZE] or ins[0].type != "tensor(float)":
        raise ValueError("Entrada incompativel: esperado float32 [1,3,640,640].")
    if len(outs) != 1 or outs[0].shape != list(EXPECTED) or outs[0].type != "tensor(float)":
        raise ValueError("Saida incompativel: esperado float32 [1,5,8400], sem NMS embutido.")
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("task") != "detect" or ast.literal_eval(meta.get("names", "{}")) != {0: "praga"}:
        raise ValueError("O ONNX deve identificar a unica classe como 0: praga e a tarefa como detect.")
    return session, ins[0].name


def _digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def exportar(pesos: Path, dataset: Path, destino: Path, origem=None):
    """Escreve destino apenas apos verificar. Devolve os hashes e as comparacoes."""
    pesos, destino = Path(pesos).resolve(), Path(destino).resolve()
    if not pesos.is_file() or pesos.suffix.lower() != ".pt":
        raise ValueError(f"Pesos .pt nao encontrados: {pesos}")
    if destino.suffix.lower() != ".onnx" or destino.exists():
        raise ValueError(f"O destino deve ser um arquivo .onnx que ainda nao existe: {destino}")
    os.environ["YOLO_AUTOINSTALL"] = "false"
    try:
        import cv2
        import numpy as np
        import onnx
        import onnxruntime as ort
        import torch
        from PIL import Image, ImageOps
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(f"Dependencia indisponivel no Python {sys.executable}: {exc}. "
                           "Para os extras ONNX, use no PowerShell: "
                           f"& '{sys.executable}' -m pip install 'onnx==1.19.0' 'onnxruntime==1.23.0'. "
                           "As demais dependencias sao as do ambiente tcc-ia usado no treino.") from exc

    samples = _amostras(dataset, origem)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="conversao-", dir=destino.parent) as temp:
        working = Path(temp)/"origem.pt"
        shutil.copyfile(pesos, working)
        hash_pt = _digest(working)
        model = YOLO(str(working))
        if model.task != "detect" or model.names != {0: "praga"}:
            raise ValueError(f"Pesos nao correspondem a V2: tarefa={model.task}, classes={model.names}.")
        torch.set_num_threads(2)
        print("Exportando YOLO11n para ONNX FP32 (CPU, opset 17)...", flush=True)
        exported = Path(model.export(format="onnx", imgsz=SIZE, batch=1, dynamic=False,
                                     simplify=False, opset=17, nms=False, half=False, device="cpu"))
        session, input_name = _checar_onnx(exported, onnx, ort)
        network = YOLO(str(working)).model.cpu().float().eval()
        comparisons = []
        with torch.inference_mode():
            for path in samples:
                tensor = _entrada(path, cv2, np, Image, ImageOps)
                ref = network(torch.from_numpy(tensor))
                if isinstance(ref, (list, tuple)):
                    ref = ref[0]
                if not torch.is_tensor(ref):
                    raise ValueError("Saida de referencia PyTorch desconhecida.")
                predicted = session.run(None, {input_name: tensor})[0]
                try:
                    values = _comparar(ref.detach().cpu().numpy(), predicted, np)
                except ValueError as exc:
                    raise ValueError(f"Falha na foto {path.name}: {exc}") from exc
                comparisons.append({"imagem": path.name, **values})
                print(f"OK: {path.name}, erro maximo de caixa {values['erro_max_caixas_entrada_px']:.5f} px "
                      f"e pontuacao {values['erro_max_pontuacao']:.6f}.", flush=True)
        hash_onnx = _digest(exported)
        # Algumas versoes do runtime mantem o arquivo aberto no Windows.
        del session
        # Ambos estao no mesmo volume. No Windows, rename falha se o destino ja existe.
        # Em POSIX, link evita o comportamento de sobrescrita de rename.
        if os.name == "nt":
            os.rename(exported, destino)
        else:
            os.link(exported, destino)
    return {"modelo_sha256": hash_onnx, "pesos_pt_sha256": hash_pt, "opset": 17,
            "classes": {0: "praga"}, "comparacoes": comparisons, "metodo": "antes do filtro de caixas; 8400 propostas por foto"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("pesos", "dataset", "destino"):
        parser.add_argument("--"+flag, type=Path, required=True)
    parser.add_argument('--origem', type=Path, help='Pasta VOC2007 ou JPEGImages original para localizar as fotos do manifesto V2.')
    args = parser.parse_args()
    try:
        print(json.dumps(exportar(args.pesos, args.dataset, args.destino, args.origem), indent=2))
    except Exception as error:
        print("Falha na exportacao:", error, file=sys.stderr)
        sys.exit(1)
