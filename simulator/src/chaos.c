#include "chaos.h"

void chaos_seed(ChaosRng *rng, unsigned int seed) {
    rng->state = seed ? seed : 0x9e3779b9u;
}

static unsigned int xorshift32(unsigned int *state) {
    unsigned int x = *state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

double chaos_next_double(ChaosRng *rng) {
    unsigned int r = xorshift32(&rng->state);
    return (double)r / 4294967296.0; /* 2^32, so result is in [0, 1) */
}

int chaos_decide_stage(ChaosRng *rng, const DeviceState *state, const char *stage_id) {
    const StageConfig *cfg = device_state_find_stage(state, stage_id);
    if (cfg == NULL) {
        return -1;
    }
    if (cfg->forced_outcome != -1) {
        return cfg->forced_outcome;
    }
    return chaos_next_double(rng) < cfg->success_probability ? 1 : 0;
}

int chaos_should_drop(const DeviceState *state, int command_index) {
    return state->drop_after_command > 0 && command_index == state->drop_after_command;
}
