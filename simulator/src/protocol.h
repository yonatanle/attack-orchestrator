#ifndef PROTOCOL_H
#define PROTOCOL_H

#include "device_state.h"
#include "chaos.h"

void protocol_handle_connection(int client_fd, ChaosRng *rng, DeviceState *state);

#endif
