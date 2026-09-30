"""Agente da Raspberry: JPEG, fila persistente e envio idempotente para /api/v1."""

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import shutil
import signal
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from uuid import UUID, uuid4


VERSION = "TCC CAMERA AGENT V1"
MAX_BYTES = 10 * 1024 * 1024
MIN_FREE = 200 * 1024 * 1024
STOP = threading.Event()
BASE_DIR = Path(__file__).resolve().parent


def log(message):
    print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_config(path):
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator:
            raise ValueError("Configuracao invalida: use NOME=valor.")
        values[name.strip()] = value.strip().strip("\"'")
    for name, value in os.environ.items():
        if name.startswith("ACELGA_"):
            values[name] = value
    api = values.get("ACELGA_API_URL", "").rstrip("/")
    parsed = urllib.parse.urlsplit(api)
    if (not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("ACELGA_API_URL deve ser a origem, como http://192.168.0.110:8000.")
    parsed.port  # Valida a porta, caso tenha sido informada.
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise ValueError("Para HTTP local, use o endereco IP do computador.") from None
        private = address.is_loopback or any(
            address.version == network.version and address in network
            for network in map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
        )
        if not private or values.get("ACELGA_ALLOW_LOCAL_HTTP") != "1":
            raise ValueError("HTTP exige rede privada e ACELGA_ALLOW_LOCAL_HTTP=1.")
    elif parsed.scheme != "https":
        raise ValueError("Use uma URL HTTP local ou HTTPS.")
    token = values.get("ACELGA_DEVICE_KEY", "")
    if len(token) < 32 or token.startswith("SUBSTITUA_") or any(char.isspace() for char in token):
        raise ValueError("ACELGA_DEVICE_KEY ausente ou invalida.")
    interval = int(values.get("ACELGA_INTERVAL_SECONDS", "900"))
    limit = int(values.get("ACELGA_MAX_QUEUE", "100"))
    if interval < 60 or not 1 <= limit <= 10000:
        raise ValueError("Intervalo minimo: 60 segundos. Limite da fila: 1 a 10000.")
    return api, token, interval, limit


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


class Agent:
    def __init__(self, base, api, token, limit=100):
        self.base, self.api, self.token, self.limit = base, api, token, limit
        self.pending = base / "pending"
        self.rejected = base / "rejected"
        self.staging = base / "staging"
        for directory in (self.pending, self.rejected, self.staging):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def reject(self, folder, reason):
        destination = self.rejected / folder.name
        if destination.exists():
            destination = self.rejected / f"{folder.name}-{uuid4()}"
        folder.rename(destination)
        sync_dir(folder.parent)
        sync_dir(self.rejected)
        (destination / "motivo.txt").write_text(reason, encoding="utf-8")
        log(f"REVISAR {folder.name}: {reason}. Arquivos preservados em rejected.")

    def read_item(self, folder):
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        if str(UUID(metadata["id"])) != folder.name:
            raise ValueError("ID da pasta diferente do registro.")
        when = datetime.fromisoformat(metadata["captured_at"])
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("Data sem fuso horario.")
        photo = folder / "image.jpg"
        if not 0 < photo.stat().st_size <= MAX_BYTES:
            raise ValueError("Foto vazia ou acima de 10 MiB.")
        payload = photo.read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
            raise ValueError("O arquivo nao corresponde ao hash salvo.")
        return metadata, payload

    def recover(self):
        # Capturas interrompidas nunca sao enviadas como se estivessem completas.
        for folder in self.staging.iterdir():
            if not folder.is_dir():
                continue
            try:
                self.read_item(folder)
            except (OSError, ValueError, KeyError, TypeError):
                self.reject(folder, "Captura interrompida ou incompleta.")
            else:
                folder.rename(self.pending / folder.name)
                sync_dir(self.staging)
                sync_dir(self.pending)

    def capture(self):
        queued = sum(1 for directory in (self.pending, self.rejected, self.staging)
                     for item in directory.iterdir() if item.is_dir())
        if queued >= self.limit:
            log("CAPTURA PAUSADA: limite da fila atingido. Os arquivos existentes foram mantidos.")
            return False
        if shutil.disk_usage(self.base).free < MIN_FREE + MAX_BYTES:
            log("CAPTURA PAUSADA: pouco espaco livre no cartao.")
            return False
        command = shutil.which("rpicam-still")
        if command is None:
            raise RuntimeError("rpicam-still nao encontrado.")
        identifier = str(uuid4())
        folder = self.staging / identifier
        folder.mkdir(mode=0o700)
        photo = folder / "image.jpg"
        captured_at = datetime.now(UTC).isoformat()
        try:
            subprocess.run([
                command, "--nopreview", "--autofocus-on-capture",
                "--width", "2304", "--height", "1296", "--encoding", "jpg",
                "--timeout", "2000", "--output", str(photo),
            ], check=True, timeout=45, stdout=subprocess.DEVNULL)
            if not 0 < photo.stat().st_size <= MAX_BYTES:
                raise ValueError("Foto vazia ou acima de 10 MiB.")
            with photo.open("rb") as source:
                payload = source.read()
                os.fsync(source.fileno())
            if not payload.startswith(b"\xff\xd8\xff"):
                raise ValueError("A camera nao produziu um JPEG.")
            write_json(folder / "metadata.json", {
                "id": identifier, "captured_at": captured_at,
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
            folder.rename(self.pending / identifier)
            sync_dir(self.staging)
            sync_dir(self.pending)
            log(f"CAPTURADA {identifier}")
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            if folder.exists():
                self.reject(folder, "Falha ao capturar; confira os logs da camera.")
            raise

    def send_pending(self):
        successful = True
        # A ordem de tentativa e a de criacao das pastas, nao a ordem aleatoria dos UUIDs.
        folders = sorted((item for item in self.pending.iterdir() if item.is_dir()),
                         key=lambda item: (item.stat().st_mtime_ns, item.name))
        for folder in folders:
            if STOP.is_set():
                return False
            try:
                metadata, payload = self.read_item(folder)
            except (OSError, ValueError, KeyError, TypeError):
                self.reject(folder, "Metadados ou imagem local invalidos.")
                successful = False
                continue
            request = urllib.request.Request(
                self.api + "/api/v1/captures", data=payload, method="POST",
                headers={"Authorization": "Bearer " + self.token,
                         "Content-Type": "image/jpeg",
                         "X-Capture-Id": metadata["id"],
                         "X-Captured-At": metadata["captured_at"]},
            )
            try:
                with self.opener.open(request, timeout=30) as response:
                    status = response.status
                    result = json.loads(response.read(65536))
                if (status not in (200, 201) or not isinstance(result, dict)
                        or result.get("id") != metadata["id"]
                        or result.get("sha256") != metadata["sha256"]
                        or result.get("size_bytes") != len(payload)):
                    raise ValueError("Confirmacao da API nao corresponde a esta foto.")
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                if code in (409, 413, 415, 422):
                    self.reject(folder, f"API recusou a captura: HTTP {code}.")
                    successful = False
                    continue
                log(f"PENDENTE {folder.name}: HTTP {code}; confira a credencial se for 401/403.")
                return False
            except (OSError, urllib.error.URLError, HTTPException, ValueError):
                log(f"PENDENTE {folder.name}: sem confirmacao valida da API. Sera reenviada com o mesmo ID.")
                return False
            # Somente uma confirmacao compativel permite remover a foto da fila.
            shutil.rmtree(folder)
            sync_dir(self.pending)
            log(f"ENVIADA {metadata['id']} HTTP {status}")
        return successful


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=BASE_DIR / ".env.raspberry")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="Captura uma foto, tenta enviar a fila e termina.")
    modes.add_argument("--send-only", action="store_true", help="Tenta reenviar a fila sem capturar outra foto.")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        api, token, interval, limit = load_config(args.env)
    except (OSError, ValueError):
        parser.exit(1, "Configuracao invalida: confira .env.raspberry, URL, chave e intervalo.\n")
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    with (BASE_DIR / ".agent.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "Ja existe um agente executando nesta pasta.\n")
        log(VERSION)
        agent = Agent(BASE_DIR, api, token, limit)
        agent.recover()
        while not STOP.is_set():
            ok = True
            try:
                if not args.send_only:
                    # Libera espaco ocupado por fotos antigas antes da nova captura.
                    ok = agent.send_pending()
                    if not STOP.is_set():
                        captured = agent.capture()
                        ok = captured and ok
                if not STOP.is_set():
                    sent = agent.send_pending()
                    ok = sent and ok
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                log(f"FALHA LOCAL: {type(error).__name__}. Confira camera e armazenamento.")
                ok = False
            if args.once or args.send_only:
                return 0 if ok else 2
            STOP.wait(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
