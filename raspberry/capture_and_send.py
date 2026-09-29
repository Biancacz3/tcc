"""Captura fotos de uma câmera Raspberry Pi e envia ao Acelga Vigia.

Configure ACELGA_API_URL (origem HTTPS), ACELGA_DEVICE_KEY e,
opcionalmente, ACELGA_INTERVAL_SECONDS. Arquivos não enviados ficam
na pasta pending e serão reenviados no próximo ciclo.
"""
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request

API_URL = os.environ.get("ACELGA_API_URL", "").rstrip("/")
DEVICE_KEY = os.environ.get("ACELGA_DEVICE_KEY", "")
INTERVAL = max(60, int(os.environ.get("ACELGA_INTERVAL_SECONDS", "900")))
PENDING = Path(__file__).resolve().parent / "pending"


def camera_command():
    command = shutil.which("rpicam-still") or shutil.which("libcamera-still")
    if not command:
        raise RuntimeError("Instale e teste rpicam-still (ou libcamera-still) na Raspberry.")
    return command


def capture():
    PENDING.mkdir(exist_ok=True)
    when = dt.datetime.now(dt.timezone.utc)
    path = PENDING / f"acelga_{when.strftime('%Y%m%dT%H%M%SZ')}.jpg"
    subprocess.run(
        [camera_command(), "-n", "-o", str(path), "--width", "1640", "--height", "1232"],
        check=True, timeout=40
    )
    return path


def send_pending():
    for path in sorted(PENDING.glob("*.jpg")):
        payload = path.read_bytes()
        if len(payload) > 6 * 1024 * 1024:
            print(f"Imagem excede 6 MB: {path.name}", flush=True)
            continue
        request = urllib.request.Request(
            f"{API_URL}/api/device/captures", data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {DEVICE_KEY}",
                "Content-Type": "image/jpeg",
                "X-Filename": path.name,
                "X-Captured-At": dt.datetime.strptime(
                    path.stem.removeprefix("acelga_"), "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=dt.timezone.utc).isoformat(),
            }
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 201:
                    raise RuntimeError(f"Resposta inesperada: {response.status}")
            path.unlink()
            print(f"Enviada: {path.name}", flush=True)
        except (urllib.error.URLError, OSError, RuntimeError) as error:
            print(f"Envio pendente ({path.name}): {error}", flush=True)
            break


def main():
    if not API_URL.startswith("https://") or not DEVICE_KEY:
        raise SystemExit("Defina ACELGA_API_URL (HTTPS) e ACELGA_DEVICE_KEY.")
    while True:
        try:
            send_pending()
            capture()
            send_pending()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"Erro de captura: {error}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
