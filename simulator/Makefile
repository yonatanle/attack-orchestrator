CC = gcc
CFLAGS = -Wall -Wextra -std=gnu11 -Isrc
SRC = src/main.c src/protocol.c src/device_state.c src/chaos.c
BIN = simulator

.PHONY: all run clean

all: $(BIN)

$(BIN): $(SRC) src/protocol.h src/device_state.h src/chaos.h
	$(CC) $(CFLAGS) -o $(BIN) $(SRC)
	chmod +x $(BIN)

run: $(BIN)
	./$(BIN) --port 9000

clean:
	rm -f $(BIN)
