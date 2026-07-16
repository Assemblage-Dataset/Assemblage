# E2E fixture: plain-make project. The builder invokes
#   timeout 10m make CFLAGS="$CFLAGS -g -DNDEBUG <opt>" CXXFLAGS=... -j16
# and command-line variable assignments override these defaults.
CC ?= cc
CFLAGS ?= -O0

hello: src/main.o src/mathlib.o
	$(CC) $(CFLAGS) -o hello src/main.o src/mathlib.o

src/%.o: src/%.c src/mathlib.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f hello src/*.o
