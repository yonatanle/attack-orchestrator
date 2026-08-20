#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "chaos.h"
#include "device_state.h"
#include "protocol.h"

/*
 * Device simulator. Behaves like a single real device over a TCP
 * connection: reports its own state, decides RUN_STAGE outcomes itself
 * (see chaos.c), serves a small fixed file tree for LIST/READ, and can be
 * configured to drop the connection mid-chain to exercise the framework's
 * disconnect handling.
 *
 * Usage:
 *   simulator [--port N] [--model NAME] [--seed N] [--drop-after N] [--force ID=success|fail ...]
 *
 * --model overrides the fixture device's reported model (default
 * "iPhone12"), so integration tests can prove DeviceRequirement.models
 * filtering against a real device that genuinely reports itself as
 * something else, not just against synthetic DeviceState objects.
 *
 * --force is repeatable (e.g. `--force pair=fail --force fast_pair=success`)
 * so a test can pin exactly the stage outcomes it needs for a deterministic
 * scenario, which is what makes the integration tests reliable instead of
 * flaky despite RUN_STAGE outcomes normally being randomized.
 *
 * One connection is served fully before the next is accepted -- this
 * simulator models one device talking to one orchestrator at a time, which
 * is all the assignment's scope calls for.
 */

#define MAX_FORCE_SPECS 16

static int parse_int_arg(const char *s, int fallback) {
    if (s == NULL) {
        return fallback;
    }
    char *end;
    long v = strtol(s, &end, 10);
    if (end == s) {
        return fallback;
    }
    return (int)v;
}

/* Applies a single "stage_id=success|fail" spec to state's stage table. */
static void apply_force_spec(DeviceState *state, const char *spec) {
    const char *eq = strchr(spec, '=');
    if (eq == NULL) {
        fprintf(stderr, "invalid --force spec (expected id=success|fail): %s\n", spec);
        return;
    }

    char stage_id[MAX_STAGE_ID_LEN];
    size_t id_len = (size_t)(eq - spec);
    if (id_len >= sizeof(stage_id)) {
        id_len = sizeof(stage_id) - 1;
    }
    memcpy(stage_id, spec, id_len);
    stage_id[id_len] = '\0';

    int outcome = (strcmp(eq + 1, "success") == 0) ? 1 : 0;

    int applied = 0;
    for (size_t j = 0; j < state->stage_count; j++) {
        if (strcmp(state->stages[j].stage_id, stage_id) == 0) {
            state->stages[j].forced_outcome = outcome;
            applied = 1;
        }
    }
    if (!applied) {
        fprintf(stderr, "warning: --force references unknown stage id: %s\n", stage_id);
    }
}

int main(int argc, char **argv) {
    int port = 9000;
    const char *model_override = NULL;
    unsigned int seed = 42;
    int drop_after_command = 0;
    const char *force_specs[MAX_FORCE_SPECS];
    int force_spec_count = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
            port = parse_int_arg(argv[++i], port);
        } else if (strcmp(argv[i], "--model") == 0 && i + 1 < argc) {
            model_override = argv[++i];
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            seed = (unsigned int)parse_int_arg(argv[++i], (int)seed);
        } else if (strcmp(argv[i], "--drop-after") == 0 && i + 1 < argc) {
            drop_after_command = parse_int_arg(argv[++i], 0);
        } else if (strcmp(argv[i], "--force") == 0 && i + 1 < argc) {
            if (force_spec_count >= MAX_FORCE_SPECS) {
                fprintf(stderr, "too many --force specs (max %d)\n", MAX_FORCE_SPECS);
                return 1;
            }
            force_specs[force_spec_count++] = argv[++i];
        } else {
            fprintf(stderr, "unknown or incomplete argument: %s\n", argv[i]);
            return 1;
        }
    }

    DeviceState state;
    device_state_init_default(&state);
    state.rng_seed = seed;
    state.drop_after_command = drop_after_command;
    if (model_override != NULL) {
        snprintf(state.model, sizeof(state.model), "%s", model_override);
    }

    for (int i = 0; i < force_spec_count; i++) {
        apply_force_spec(&state, force_specs[i]);
    }

    signal(SIGPIPE, SIG_IGN); /* a client dropping mid-write shouldn't kill the process */

    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        perror("socket");
        return 1;
    }

    int reuse = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons((unsigned short)port);

    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(listen_fd);
        return 1;
    }

    if (listen(listen_fd, 1) < 0) {
        perror("listen");
        close(listen_fd);
        return 1;
    }

    fprintf(stderr,
            "device simulator listening on port %d (model=%s ios=%s battery=%d seed=%u "
            "drop_after=%d)\n",
            port, state.model, state.ios_version, state.battery_level, seed,
            drop_after_command);
    fflush(stderr);

    ChaosRng rng;
    chaos_seed(&rng, state.rng_seed);

    for (;;) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);
        int client_fd = accept(listen_fd, (struct sockaddr *)&client_addr, &client_len);
        if (client_fd < 0) {
            if (errno == EINTR) {
                continue;
            }
            perror("accept");
            break;
        }
        protocol_handle_connection(client_fd, &rng, &state);
    }

    close(listen_fd);
    return 0;
}
