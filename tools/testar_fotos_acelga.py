#!/usr/bin/env python3
"""Diagnostico em lote; nao altera limiares, pesos, originais, API ou motores."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import zipfile

VERSION = "teste-lote-acelga-v1"
FILES_SEG = ("comparacao.png", "resumo.json", "mascara.png", "mascara-rejeitada.png", "recorte-experimental.png")
FILES_IA = ("comparacao-ia.png", "resumo-ia.json")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(command, log):
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=180, env=env)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        code, stdout, stderr = 124, decode(exc.stdout), decode(exc.stderr) + "\nTempo limite de 180 segundos excedido."
    log.write_text(stdout + "\n--- STDERR ---\n" + stderr, encoding="utf-8")
    return code, stdout


def collect(stdout, photo, destination, prefix, names):
    paths = [line.removeprefix("Saida: ").strip() for line in stdout.splitlines() if line.startswith("Saida: ")]
    if not paths:
        return None
    source = Path(paths[-1]).resolve()
    if source.parent != photo.parent or not source.name.startswith(prefix) or not source.is_dir():
        raise ValueError("Pasta de diagnostico inesperada; nao copiar arquivos.")
    for name in names:
        file = source / name
        if file.is_file() and file.resolve().parent == source:
            shutil.copyfile(file, destination / name)
    return source


def read_report(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8-sig"))


def run_batch(folder, project, marker):
    folder, project = folder.resolve(strict=True), project.resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("--pasta deve indicar uma pasta de fotografias.")
    photos = sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                    and p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not photos:
        raise ValueError("Nenhum JPEG/PNG encontrado diretamente na pasta.")
    scripts = {"segmentacao": project / "tools" / "delimitar_acelga_v3.py",
               "comparacao": project / "tools" / "comparar_ia_acelga.py"}
    hashes = {name: digest(path) for name, path in scripts.items()}
    model = project / "artifacts" / "models" / "pulgao-v1.onnx"
    expected_model = digest(model) if model.is_file() else None
    output = Path(tempfile.mkdtemp(prefix="teste-lote-acelga-", dir=folder.parent))
    report = {"versao": VERSION, "status": "em_execucao", "criado_em": datetime.now(timezone.utc).isoformat(),
              "marcador_alvo": marker, "scripts_sha256": hashes, "modelo_local_sha256_inicial": expected_model,
              "fotos": [], "avisos": [], "limites": [
                  "Diagnosticos, nao fotos aprovadas ou validacao do reconhecimento de pulgoes.",
                  "Pontos pretos em plantas artificiais nao comprovam deteccao de pulgoes reais.",
                  "Falha de delimitacao nao equivale a zero deteccoes.",
                  "ZIP omite arquivos originais; comparacoes incluem miniaturas da cena."]}
    print("Saida do lote: " + str(output), flush=True)
    for index, photo in enumerate(photos, 1):
        name = f"{index:03d}-" + re.sub(r"[^A-Za-z0-9._-]", "_", photo.stem)[:60]
        item_folder = output / name
        item_folder.mkdir()
        item = {"foto": photo.name, "pasta_resultados": name, "status": "falha", "sha256_original": digest(photo)}
        report["fotos"].append(item)
        print(f"[{index}/{len(photos)}] {photo.name}", flush=True)
        try:
            code, stdout = execute([sys.executable, str(scripts["segmentacao"]), str(photo),
                                    "--marcador", str(marker)], item_folder / "segmentacao.log.txt")
            item["codigo_segmentacao"] = code
            collect(stdout, photo, item_folder, "diagnostico-acelga-", FILES_SEG)
            seg = read_report(item_folder, "resumo.json") if (item_folder / "resumo.json").exists() else {}
            item["status_segmentacao"] = seg.get("status", "sem_relatorio")
            if code != 0 or seg.get("status") != "revisao_visual_necessaria" or seg.get("validacao_geometrica", {}).get("rejeitada") is not False:
                item.update(status="delimitacao_nao_concluida", erro=seg.get("erro", "Confira segmentacao.log.txt"))
                continue
            code, stdout = execute([sys.executable, str(scripts["comparacao"]), "--imagem", str(photo),
                                    "--diagnostico", str(item_folder), "--projeto", str(project)], item_folder / "ia.log.txt")
            item["codigo_ia"] = code
            collect(stdout, photo, item_folder, "comparacao-ia-acelga-", FILES_IA)
            ia = read_report(item_folder, "resumo-ia.json") if (item_folder / "resumo-ia.json").exists() else {}
            if code != 0 or ia.get("status") != "concluido":
                item.update(status="comparacao_nao_concluida", erro=ia.get("erro", "Confira ia.log.txt"))
                continue
            item.update(status="diagnosticos_gerados", modelo_sha256=ia["backend"]["modelo_sha256"],
                        limiar=ia["backend"]["limiar"], contagens={r["variante"]: r["quantidade"] for r in ia["resultados"]})
        except Exception as exc:
            item["erro"] = f"{type(exc).__name__}: {exc}"
        finally:
            item["original_preservado"] = photo.is_file() and digest(photo) == item["sha256_original"]
            (output / "resumo-lote.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    models = {r["modelo_sha256"] for r in report["fotos"] if "modelo_sha256" in r}
    if len(models) > 1 or any(value != expected_model for value in models):
        report["avisos"].append("Pesos variaram entre execucoes ou diferem do arquivo inicial; nao tratar como comparacao uniforme.")
    if any(not r.get("original_preservado") for r in report["fotos"]):
        report["avisos"].append("Uma entrada foi alterada durante o lote; verificar original_preservado por foto.")
    report["status"] = "diagnosticos_gerados"
    report["comparacoes_concluidas"] = sum(r["status"] == "diagnosticos_gerados" for r in report["fotos"])
    report["nao_concluidas"] = len(photos) - report["comparacoes_concluidas"]
    (output / "resumo-lote.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = output / "diagnosticos.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(output.rglob("*")):
            if file.is_file() and file.suffix in {".json", ".png", ".txt"}:
                bundle.write(file, file.relative_to(output))
    print(f"DIAGNOSTICOS GERADOS: {report['comparacoes_concluidas']} comparacoes concluidas; {report['nao_concluidas']} nao concluidas.")
    print("Envie: " + str(archive))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pasta", type=Path, required=True)
    parser.add_argument("--projeto", type=Path, required=True)
    parser.add_argument("--marcador", type=int, choices=(10, 11, 20, 21), default=21)
    args = parser.parse_args()
    try:
        run_batch(args.pasta, args.projeto, args.marcador)
    except (Exception, KeyboardInterrupt) as exc:
        print(f"LOTE INTERROMPIDO: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
