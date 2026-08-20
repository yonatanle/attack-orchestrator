#ifndef DEVICE_STATE_H
#define DEVICE_STATE_H

#include <stddef.h>

#define MAX_STAGE_ID_LEN 32
#define MAX_PATH_LEN 256
#define MAX_STAGES 16
#define MAX_FILES 32

typedef struct {
    char stage_id[MAX_STAGE_ID_LEN];
    double success_probability;
    int forced_outcome;
} StageConfig;

typedef struct {
    char path[MAX_PATH_LEN];
    int is_dir;
    const unsigned char *content;
    size_t content_len;
} FileEntry;

typedef struct {
    char model[64];
    char ios_version[32];
    int battery_level;
    unsigned int rng_seed;
    int drop_after_command;

    StageConfig stages[MAX_STAGES];
    size_t stage_count;

    FileEntry files[MAX_FILES];
    size_t file_count;

    int unlocked; /* gates LIST/READ: becomes 1 only on an explicit UNLOCK command */
} DeviceState;

void device_state_init_default(DeviceState *state);
const StageConfig *device_state_find_stage(const DeviceState *state, const char *stage_id);
const FileEntry *device_state_find_file(const DeviceState *state, const char *path);
int device_state_dir_exists(const DeviceState *state, const char *dir_path);
size_t device_state_list_dir(const DeviceState *state, const char *dir_path, const FileEntry **out_entries, size_t max_entries);

#endif
