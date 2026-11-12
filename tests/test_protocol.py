"""Protocol checks that do not contact a Redis service."""

import importlib.util
from io import BytesIO
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "redis-rogue-server.py"
SPEC = importlib.util.spec_from_file_location("rogue_server", SOURCE)
rogue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rogue)


class ProtocolTests(unittest.TestCase):
    def test_command_uses_utf8_byte_length(self):
        self.assertEqual(
            rogue.encode_command("system.exec", "printf café"),
            b"*2\r\n$11\r\nsystem.exec\r\n$12\r\nprintf caf\xc3\xa9\r\n",
        )

    def test_reply_frames_and_error(self):
        stream = BytesIO(b"+PONG\r\n*2\r\n$3\r\ndir\r\n$4\r\n/tmp\r\n")
        self.assertEqual(rogue.reply(stream), "PONG")
        self.assertEqual(rogue.reply(stream), ["dir", "/tmp"])
        with self.assertRaisesRegex(rogue.RedisError, "loading"):
            rogue.reply(BytesIO(b"-ERR loading failed\r\n"))

    def test_replication_commands_can_share_stream(self):
        stream = BytesIO(
            b"*1\r\n$4\r\nPING\r\n"
            b"*3\r\n$8\r\nREPLCONF\r\n$14\r\nlistening-port\r\n$4\r\n6379\r\n"
            b"*3\r\n$5\r\nPSYNC\r\n$1\r\n?\r\n$2\r\n-1\r\n"
        )
        self.assertEqual(rogue.replication_command(stream), ["PING"])
        self.assertEqual(rogue.replication_command(stream), ["REPLCONF", "listening-port", "6379"])
        self.assertEqual(rogue.replication_command(stream), ["PSYNC", "?", "-1"])


if __name__ == "__main__":
    unittest.main()
