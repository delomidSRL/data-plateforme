import io
from contextlib import contextmanager
from dataclasses import dataclass

import paramiko

CONNECT_TIMEOUT = 8
COMMAND_TIMEOUT = 20


class SSHError(Exception):
    pass


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@contextmanager
def ssh_session(hostname: str, port: int, username: str, auth_method: str, secret: str):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if auth_method == "key":
            key_file = io.StringIO(secret)
            pkey = None
            for loader in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
                try:
                    key_file.seek(0)
                    pkey = loader.from_private_key(key_file)
                    break
                except paramiko.SSHException:
                    continue
            if pkey is None:
                raise SSHError("Clé privée SSH invalide ou format non supporté.")
            client.connect(hostname=hostname, port=port, username=username, pkey=pkey, timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT, auth_timeout=CONNECT_TIMEOUT)
        else:
            client.connect(hostname=hostname, port=port, username=username, password=secret, timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT, auth_timeout=CONNECT_TIMEOUT)
        yield client
    except paramiko.AuthenticationException:
        raise SSHError("Authentification SSH refusée : vérifiez l'utilisateur et les identifiants.")
    except (paramiko.SSHException, OSError, EOFError) as exc:
        raise SSHError(f"Connexion SSH impossible : {exc}")
    finally:
        client.close()


def run_command(client: paramiko.SSHClient, command: str, timeout: int = COMMAND_TIMEOUT) -> CommandResult:
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout.read().decode(errors="replace").strip(),
            stderr=stderr.read().decode(errors="replace").strip(),
        )
    except Exception as exc:
        raise SSHError(f"Échec de la commande distante : {exc}")


def test_connection(hostname: str, port: int, username: str, auth_method: str, secret: str) -> tuple[bool, str, str | None]:
    """Returns (reachable, message, docker_version)."""
    try:
        with ssh_session(hostname, port, username, auth_method, secret) as client:
            result = run_command(client, "docker --version")
            if not result.ok:
                return False, "Connecté en SSH, mais Docker est introuvable sur le serveur.", None
            return True, "Connexion réussie.", result.stdout
    except SSHError as exc:
        return False, str(exc), None


def write_remote_file(hostname: str, port: int, username: str, auth_method: str, secret: str, remote_path: str, content: str) -> None:
    with ssh_session(hostname, port, username, auth_method, secret) as client:
        run_command(client, f"mkdir -p $(dirname {remote_path})")
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as f:
                f.write(content)
        finally:
            sftp.close()
