#include "device_state.h"

#include <string.h>
#include <stdio.h>

/* Small fixed fixture "filesystem" -- enough to exercise LIST/READ and
   extract_all's recursive walk without touching the real filesystem. */
static const unsigned char CONTACTS_CONTENT[] =
    "alice,+1-555-0100\n"
    "bob,+1-555-0142\n";
static const unsigned char MESSAGES_CONTENT[] =
    "[2026-08-01] alice: hey\n"
    "[2026-08-02] bob: yo\n";
static const unsigned char IMG1_CONTENT[] = "FAKEJPEGDATA-img1";
static const unsigned char IMG2_CONTENT[] = "FAKEJPEGDATA-img2";

void device_state_init_default(DeviceState *state) {
    memset(state, 0, sizeof(*state));

    snprintf(state->model, sizeof(state->model), "iPhone12");
    snprintf(state->ios_version, sizeof(state->ios_version), "15.4");
    state->battery_level = 80;
    state->rng_seed = 42;
    state->drop_after_command = 0;

    size_t i = 0;

    snprintf(state->files[i].path, MAX_PATH_LEN, "/photos");
    state->files[i].is_dir = 1;
    state->files[i].content = NULL;
    state->files[i].content_len = 0;
    i++;

    snprintf(state->files[i].path, MAX_PATH_LEN, "/contacts.db");
    state->files[i].is_dir = 0;
    state->files[i].content = CONTACTS_CONTENT;
    state->files[i].content_len = sizeof(CONTACTS_CONTENT) - 1;
    i++;

    snprintf(state->files[i].path, MAX_PATH_LEN, "/messages.db");
    state->files[i].is_dir = 0;
    state->files[i].content = MESSAGES_CONTENT;
    state->files[i].content_len = sizeof(MESSAGES_CONTENT) - 1;
    i++;

    snprintf(state->files[i].path, MAX_PATH_LEN, "/photos/img1.jpg");
    state->files[i].is_dir = 0;
    state->files[i].content = IMG1_CONTENT;
    state->files[i].content_len = sizeof(IMG1_CONTENT) - 1;
    i++;

    snprintf(state->files[i].path, MAX_PATH_LEN, "/photos/img2.jpg");
    state->files[i].is_dir = 0;
    state->files[i].content = IMG2_CONTENT;
    state->files[i].content_len = sizeof(IMG2_CONTENT) - 1;
    i++;

    state->file_count = i;

    size_t s = 0;

    snprintf(state->stages[s].stage_id, MAX_STAGE_ID_LEN, "pair");
    state->stages[s].success_probability = 0.95;
    state->stages[s].forced_outcome = -1;
    s++;

    snprintf(state->stages[s].stage_id, MAX_STAGE_ID_LEN, "bypass_lock");
    state->stages[s].success_probability = 0.7;
    state->stages[s].forced_outcome = -1;
    s++;

    snprintf(state->stages[s].stage_id, MAX_STAGE_ID_LEN, "elevate");
    state->stages[s].success_probability = 0.6;
    state->stages[s].forced_outcome = -1;
    s++;

    snprintf(state->stages[s].stage_id, MAX_STAGE_ID_LEN, "fast_pair");
    state->stages[s].success_probability = 0.9;
    state->stages[s].forced_outcome = -1;
    s++;

    snprintf(state->stages[s].stage_id, MAX_STAGE_ID_LEN, "brute_pin");
    state->stages[s].success_probability = 0.3;
    state->stages[s].forced_outcome = -1;
    s++;

    state->stage_count = s;
}

const StageConfig *device_state_find_stage(const DeviceState *state, const char *stage_id) {
    for (size_t i = 0; i < state->stage_count; i++) {
        if (strcmp(state->stages[i].stage_id, stage_id) == 0) {
            return &state->stages[i];
        }
    }
    return NULL;
}

const FileEntry *device_state_find_file(const DeviceState *state, const char *path) {
    for (size_t i = 0; i < state->file_count; i++) {
        if (strcmp(state->files[i].path, path) == 0) {
            return &state->files[i];
        }
    }
    return NULL;
}

int device_state_dir_exists(const DeviceState *state, const char *dir_path) {
    if (strcmp(dir_path, "/") == 0) {
        return 1;
    }
    const FileEntry *entry = device_state_find_file(state, dir_path);
    return entry != NULL && entry->is_dir;
}

static void build_prefix(const char *dir_path, char *out, size_t out_size) {
    if (strcmp(dir_path, "/") == 0) {
        snprintf(out, out_size, "/");
    } else {
        snprintf(out, out_size, "%s/", dir_path);
    }
}

size_t device_state_list_dir(const DeviceState *state, const char *dir_path,
                              const FileEntry **out_entries, size_t max_entries) {
    char prefix[MAX_PATH_LEN + 1];
    build_prefix(dir_path, prefix, sizeof(prefix));
    size_t prefix_len = strlen(prefix);

    size_t count = 0;
    for (size_t i = 0; i < state->file_count && count < max_entries; i++) {
        const char *path = state->files[i].path;
        if (strncmp(path, prefix, prefix_len) != 0) {
            continue;
        }
        const char *remainder = path + prefix_len;
        if (remainder[0] == '\0') {
            continue; /* dir_path matched itself exactly, not a child */
        }
        if (strchr(remainder, '/') != NULL) {
            continue; /* nested deeper than a direct child */
        }
        out_entries[count++] = &state->files[i];
    }
    return count;
}
