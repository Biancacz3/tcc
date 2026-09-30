"""Teste local da API dentro do container. Nao imprime credenciais."""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


BASE = "http://127.0.0.1:8000/api/v1"


def call(path, method="GET", token=None, body=None, headers=None):
    values = dict(headers or {})
    if token is not None:
        values["Authorization"] = "Bearer " + token
    request = Request(BASE + path, method=method, data=body, headers=values)
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def check(label, result, expected):
    status, body = result
    if status != expected:
        raise SystemExit(f"FALHOU: {label}: HTTP {status}, esperado {expected}. {body.decode(errors='replace')[:500]}")
    print(f"OK: {label}: HTTP {status}")
    return body


def probe_size_limit(token, headers):
    # Testa a rejeicao antecipada pelo Content-Length sem transmitir 10 MiB.
    # Expect: 100-continue permite que a API recuse o corpo antes do envio.
    # E necessario receber HTTP 413 real: reset ou timeout NAO contam como sucesso.
    target = urlsplit(BASE)
    connection = HTTPConnection(target.hostname, target.port or 80, timeout=15)
    try:
        connection.putrequest("POST", target.path + "/captures")
        values = headers | {
            "Authorization": "Bearer " + token,
            "Content-Length": str(10 * 1024 * 1024 + 1),
            "Expect": "100-continue",
            "Connection": "close",
        }
        for name, value in values.items():
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def main():
    print("TESTE DE CAPTURAS - VERSAO CORRIGIDA V2", flush=True)
    if len(sys.argv) != 3:
        raise SystemExit("Uso: python testar_captura.py CAMINHO_JPEG UUID_DISPOSITIVO")
    path = Path(sys.argv[1])
    image = path.read_bytes()
    if not image or len(image) > 10 * 1024 * 1024:
        raise SystemExit("A foto de teste precisa ter entre 1 byte e 10 MiB.")
    device_id = sys.argv[2]
    admin = os.environ["ADMIN_API_TOKEN"]
    issued_body = check("Emitir nova chave do dispositivo", call(
        f"/devices/{device_id}/token", method="POST", token=admin,
    ), 200)
    token = json.loads(issued_body)["token"]
    # Guarde a chave ANTES dos testes: emiti-la invalida a chave anterior.
    credentials = Path("/tmp/raspberry.env")
    credentials.touch(mode=0o600, exist_ok=True)
    credentials.chmod(0o600)
    credentials.write_text("ACELGA_DEVICE_KEY=" + token + "\n", encoding="utf-8")
    check("Identificar dispositivo", call("/devices/me", token=token), 200)
    capture_id = str(uuid4())
    captured_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    headers = {
        "Content-Type": "image/jpeg",
        "X-Capture-Id": capture_id,
        "X-Captured-At": captured_at.isoformat(),
    }

    def upload(body=image, auth=token, extra=None):
        return call("/captures", "POST", auth, body, headers | (extra or {}))

    check("Envio sem credencial", upload(auth=None), 401)
    check("Chave administrativa nao autentica uma camera", upload(auth=admin), 401)
    check("Arquivo falso", upload(body=b"isto nao e JPEG", extra={"X-Capture-Id": str(uuid4())}), 422)
    check("Data sem fuso", upload(extra={"X-Captured-At": "2026-09-30T10:00:00"}), 422)
    check("Tamanho declarado acima de 10 MiB", probe_size_limit(token, headers), 413)
    first = json.loads(check("Primeiro envio", upload(), 201))
    second = json.loads(check("Reenvio identico", upload(), 200))
    if first["id"] != second["id"] or first["sha256"] != hashlib.sha256(image).hexdigest():
        raise SystemExit("FALHOU: identidade ou hash da captura divergente.")
    check("Mesmo ID com outra data", upload(extra={
        "X-Captured-At": (captured_at + timedelta(seconds=1)).isoformat()
    }), 409)
    check("Listagem sem credencial", call("/captures"), 401)
    listed = json.loads(check("Listagem administrativa", call("/captures", token=admin), 200))
    if sum(item["id"] == capture_id for item in listed) != 1:
        raise SystemExit("FALHOU: captura ausente ou duplicada na listagem.")
    image_path = f"/captures/{capture_id}/image"
    check("Imagem sem credencial", call(image_path), 401)
    downloaded = check("Baixar imagem como administrador", call(image_path, token=admin), 200)
    if downloaded != image:
        raise SystemExit("FALHOU: os bytes recebidos sao diferentes dos enviados.")
    print("TODOS OS TESTES PASSARAM.")
    print("Captura:", capture_id)
    print("Dispositivo:", device_id)
    print("Resolucao:", first["width"], "x", first["height"])
    print("Chave guardada em /tmp/raspberry.env; copie-a para .env.raspberry no Windows.")


if __name__ == "__main__":
    main()
