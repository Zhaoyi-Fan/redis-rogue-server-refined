CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=gnu11

.PHONY: all clean
all: module.so

module.so: module.c redismodule.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ module.c

clean:
	rm -f module.so
