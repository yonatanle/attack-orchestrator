#ifndef CHAOS_H
#define CHAOS_H

#include "device_state.h"

typedef struct {
    unsigned int state;
} ChaosRng;

void chaos_seed(ChaosRng *rng, unsigned int seed);
double chaos_next_double(ChaosRng *rng); /* uniform in [0, 1) */

/* Rolls (or applies a forced override for) stage_id's outcome.
   Returns 1 = success, 0 = fail, -1 = stage_id not found. */
int chaos_decide_stage(ChaosRng *rng, const DeviceState *state, const char *stage_id);

/* Whether the connection should be silently dropped after processing
   `command_index` commands on it (chaos-injected mid-chain disconnect). */
int chaos_should_drop(const DeviceState *state, int command_index);

#endif
