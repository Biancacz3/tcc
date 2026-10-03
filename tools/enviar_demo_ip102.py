"""Importa um JPEG de demonstracao pela API local, sem usar a chave da Raspberry.

Executar dentro do container api. A data registrada e a data da importacao,
nao a data original de captura do IP102. A mesma imagem nao e importada de novo.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener
from uuid import NAMESPACE_URL, uuid5

from PIL import Image


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python enviar_demo_ip102.py /tmp/demo-pulgao.jpg")
    token = os.environ.get("ADMIN_API_TOKEN")
    if not token:
        raise SystemExit("Execute este script dentro do container api.")
    with Path(sys.argv[1]).open("rb") as file:
        data = file.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        raise SystemExit("A foto deve ter ate 10 MiB.")
    with Image.open(BytesIO(data), formats=["JPEG"]) as img:
        if img.width * img.height > 20_000_000 or img.getexif().get(274, 1) != 1:
            raise SystemExit("Confira a resolucao e a orientacao da foto.")
        img.load()
    digest = hashlib.sha256(data).hexdigest()
    capture_id = str(uuid5(NAMESPACE_URL, "tcc:demonstracao-ip102:v1:" + digest))
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(path, key, method="GET", body=None, extra=None, binary=False):
        headers = {"Authorization": "Bearer " + key}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        req = Request("http://127.0.0.1:8000/api/v1" + path, data=body, headers=headers, method=method)
        with opener.open(req, timeout=60) as response:
            result = response.read()
            return result if binary else json.loads(result)

    try:
        previous = call(f"/captures/{capture_id}/image", token, binary=True)
    except HTTPError as error:
        if error.code != 404:
            raise
    else:
        if hashlib.sha256(previous).hexdigest() != digest:
            raise SystemExit("O ID de demonstracao ja existe com outro conteudo.")
        print("Esta foto de demonstracao ja esta no painel.")
        print("ID:", capture_id)
        return
    device = call("/devices", token, "POST", {"name": "Demonstracao IP102 (importacao manual)"})
    device_token = call(f"/devices/{device['id']}/token", token, "POST")["token"]
    capture = call("/captures", device_token, "POST", data, {
        "Content-Type": "image/jpeg", "X-Capture-Id": capture_id,
        "X-Captured-At": datetime.now(timezone.utc).isoformat(),
    })
    if capture.get("id") != capture_id or capture.get("sha256") != digest:
        raise SystemExit("A resposta da API nao confirmou a foto enviada.")
    print("FOTO DE DEMONSTRACAO ENVIADA.")
    print("Dispositivo: Demonstracao IP102 (importacao manual)")
    print("ID:", capture_id)
    print("Data registrada: importacao; nao e uma captura fisica da Raspberry.")
    print("Atualize o painel, abra a foto e clique em Analisar com IA.")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"A API respondeu HTTP {error.code}. Envie essa mensagem para conferir a falha.") from None
    except (URLError, OSError, ValueError) as error:
        raise SystemExit(f"Falha na importacao: {type(error).__name__}. Confira o arquivo e se a API esta ligada.") from None
