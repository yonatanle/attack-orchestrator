#include "protocol.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static void send_line(int fd, const char *line) {
    write(fd, line, strlen(line));
}

static void handle_state(int fd, const DeviceState *state) {
    char buf[256];
    snprintf(buf, sizeof(buf), "STATE model=%s ios=%s battery=%d\n",
             state->model, state->ios_version, state->battery_level);
    send_line(fd, buf);
}

static void handle_run_stage(int fd, ChaosRng *rng, const DeviceState *state,
                              int have_arg, const char *stage_id) {
    if (!have_arg) {
        send_line(fd, "ERR missing_stage_id\n");
        return;
    }
    int outcome = chaos_decide_stage(rng, state, stage_id);
    if (outcome < 0) {
        send_line(fd, "ERR unknown_stage\n");
    } else if (outcome == 1) {
        send_line(fd, "OK\n");
    } else {
        send_line(fd, "FAIL stage_did_not_land\n");
    }
}

static void handle_list(int fd, const DeviceState *state, int have_arg, const char *path) {
    if (!have_arg) {
        send_line(fd, "ERR missing_path\n");
        return;
    }
    if (!device_state_dir_exists(state, path)) {
        send_line(fd, "ERR not_found\n");
        return;
    }

    const FileEntry *entries[MAX_FILES];
    size_t count = device_state_list_dir(state, path, entries, MAX_FILES);

    char header[64];
    snprintf(header, sizeof(header), "OK %zu\n", count);
    send_line(fd, header);

    for (size_t i = 0; i < count; i++) {
        const char *name = strrchr(entries[i]->path, '/');
        name = name ? name + 1 : entries[i]->path;
        char entry_line[MAX_PATH_LEN + 4];
        snprintf(entry_line, sizeof(entry_line), "%s %s\n",
                 entries[i]->is_dir ? "D" : "F", name);
        send_line(fd, entry_line);
    }
}

static void handle_read(int fd, const DeviceState *state, int have_arg, const char *path) {
    if (!have_arg) {
        send_line(fd, "ERR missing_path\n");
        return;
    }
    const FileEntry *file = device_state_find_file(state, path);
    if (file == NULL || file->is_dir) {
        send_line(fd, "ERR not_found\n");
        return;
    }
    char header[64];
    snprintf(header, sizeof(header), "OK %zu\n", file->content_len);
    send_line(fd, header);
    write(fd, file->content, file->content_len);
    write(fd, "\n", 1);
}

/*
 * Reads the connection byte by byte into line_buf, dispatching one command
 * per newline. Kept intentionally simple (no separate line-reader struct) --
 * this is the only place in the process reading from the socket, and one
 * connection is fully handled here before the caller (main.c) accepts the
 * next, so there's no concurrent access to worry about.
 */
void protocol_handle_connection(int client_fd, ChaosRng *rng, DeviceState *state) {
    char buf[1024];
    int bytes_read;
    char line_buf[2048];
    int line_len = 0;
    int command_index = 0;

    while ((bytes_read = read(client_fd, buf, sizeof(buf))) > 0) {
        for (int i = 0; i < bytes_read; i++) {
            char c = buf[i];
            if (c == '\n') {
                line_buf[line_len] = '\0';
                command_index++;

                /* command_index counts completed lines on *this* connection,
                   starting at 1 for the first one -- so `--drop-after N`
                   drops the connection right as the Nth command is received,
                   before it's dispatched or answered at all. Resets to 0 on
                   every new connection (a client that reconnects after a
                   drop gets a fresh count, not a continuation of the old
                   one). */
                if (chaos_should_drop(state, command_index)) {
                    close(client_fd);
                    return;
                }

                char *cmd = line_buf;
                char *arg = NULL;
                char *space = strchr(line_buf, ' ');
                if (space) {
                    *space = '\0';
                    arg = space + 1;
                }
                int have_arg = (arg != NULL && arg[0] != '\0');

                if (strcmp(cmd, "STATE") == 0) {
                    handle_state(client_fd, state);
                } else if (strcmp(cmd, "RUN_STAGE") == 0) {
                    handle_run_stage(client_fd, rng, state, have_arg, arg);
                } else if (strcmp(cmd, "UNLOCK") == 0) {
                    /* Sent by the framework only once a full chain actually
                       completes -- not inferred here from any single
                       RUN_STAGE success, since this process has no concept
                       of which stages belong to the same chain. */
                    state->unlocked = 1;
                    send_line(client_fd, "OK\n");
                } else if (strcmp(cmd, "LIST") == 0) {
                    /* The not_unlocked check lives here, in dispatch, rather
                       than inside handle_list/handle_read themselves -- both
                       commands need the exact same gate, and putting it at
                       the one place that already knows which command is
                       being handled avoids duplicating the check (and the
                       risk of one handler forgetting it) inside each. */
                    if (!state->unlocked) {
                        send_line(client_fd, "ERR not_unlocked\n");
                    } else {
                        handle_list(client_fd, state, have_arg, arg);
                    }
                } else if (strcmp(cmd, "READ") == 0) {
                    if (!state->unlocked) {
                        send_line(client_fd, "ERR not_unlocked\n");
                    } else {
                        handle_read(client_fd, state, have_arg, arg);
                    }
                } else if (strcmp(cmd, "QUIT") == 0) {
                    close(client_fd);
                    return;
                } else {
                    send_line(client_fd, "ERR unknown_command\n");
                }

                line_len = 0;
            } else {
                /* Bytes past the buffer are silently dropped rather than
                   treated as an error -- an oversized line still eventually
                   gets dispatched (as whatever fit), just truncated, instead
                   of killing the connection. Given this protocol's fixed,
                   small fixture data, no legitimate command ever approaches
                   this limit; it exists to survive a malformed/oversized
                   line, not to support long input. */
                if (line_len < (int)sizeof(line_buf) - 1) {
                    line_buf[line_len++] = c;
                }
            }
        }
    }
}
