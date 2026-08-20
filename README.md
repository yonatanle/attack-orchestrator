# Multi-Stage Attack Orchestrator

Runs unlocking attacks against mobile devices to extract data from them. An
attack is a sequence of stages, any stage can fail, and more than one attack
may be viable for a given device -- so this framework decides which one to
run, runs it, recovers from a dropped connection if it can, and extracts
data from the device once a chain succeeds.

```
attack-orchestrator/
├── README.md
├── pyproject.toml     (pytest config only -- see "Building and running")
├── conftest.py         (puts framework/ on sys.path for tests)
├── framework/           -- Part 1: the Python framework
│   ├── orchestrator/
│   └── examples/run_demo.py
├── simulator/            -- Part 2: the C device simulator
│   ├── Makefile
│   └── src/
└── tests/                  -- Part 3: Python tests
    ├── unit/                  (FakeDeviceClient, no build/network needed)
    └── integration/            (the real compiled simulator, real TCP)
```

- `framework/orchestrator/` -- the framework. Talks to a device only through
  a small `DeviceClient` interface, never to a socket directly.
- `simulator/` -- a C device simulator speaking a small TCP protocol,
  standing in for a real device.
- `tests/` -- unit tests (fast, in-memory) and integration tests (the real
  compiled simulator, driven over a real socket).

## Architecture

```
                         AttackOrchestrator
                        /                  \
              AttackSelector          DeviceClient (interface)
             (pick an attack)          /                    \
                                FakeDeviceClient       TCPDeviceClient
                              (in-memory, Part 1          |  protocol.py
                               & unit tests)               |  (wire format)
                                                            |
                                                     TCP, newline-delimited
                                                            |
                                                  simulator/ (C, Part 2)
                                                  device_state + chaos
```

`AttackOrchestrator` and `ExtractionSession` only ever call methods on
`DeviceClient` -- `get_state`, `run_stage`, `unlock`, `read_file`, `list_dir`,
`reconnect`, `close`. They cannot tell whether they're driving an in-memory
fake or a real socket to the C simulator. That seam is what let Part 1 get
built and fully unit-tested before Part 2 (or any network code) existed at
all, per the assignment's "should not talk to a real device yet."

## Design decisions

A lot of this assignment is open-ended on purpose ("how you handle them is
up to you"). Here's what I chose and why, especially anywhere I saw more
than one reasonable option.

### Who decides whether a stage succeeds

**The device does, not the framework.** `Stage.expected_success_probability`
is the framework's *prior belief* about a stage, used for two things:

1. `AttackSelector` scores candidate attacks by it, to choose between them.
2. `FakeDeviceClient` can also roll against it directly -- so a Part-1-only
   demo, with no simulator or C code involved at all, still genuinely
   exercises "stages that probabilistically succeed or fail," satisfying
   that requirement standalone.

But once a real device (or `TCPDeviceClient`/the simulator) is in the loop,
*it* decides the actual outcome of `RUN_STAGE` -- the simulator holds its
own configured probabilities (or forced overrides), which may deliberately
differ from the framework's estimate. I chose this over having the
framework roll its own dice and just telling the device "I succeeded"
because it's what makes the C simulator meaningfully part of the attack
(it's the thing being attacked, so it should be the one whose response you
can't fully predict from the client side) rather than just a file server
behind a state query.

### Attack lifecycle

`Attack` exposes three lifecycle points, and they're intentionally not
symmetric:

- **`is_compatible(state)`** -- the "is this even worth trying" check,
  delegating to a declarative `DeviceRequirement` (see below). Always runs,
  can't be skipped, filters the candidate list before selection even sees
  it.
- **`init_attack(device_client, state)`** (optional) -- runs once, right
  after this attack is chosen, before its first stage. For anything a stage
  chain needs set up first (allocate a session token, snapshot device
  state). If it raises, the attempt is marked failed with that reason and
  the orchestrator falls back to the next candidate -- exactly like a
  failed stage. It deliberately does *not* get the stage-level
  reconnect-and-retry treatment (see "handling a dropped connection"
  below); any exception, including a dropped connection, is just "this
  attempt didn't happen."
- **stages** -- the actual chain, unchanged from before: ordered, abort on
  first failure, see "what a failed stage means" below.
- **`teardown(device_client, state, result)`** (optional) -- runs once
  after the attempt finishes, on *both* the success and failure path (it
  may need to revert partial device state either way), but only if
  `init_attack` didn't itself fail -- if there was nothing set up, there's
  nothing to tear down. A `teardown` exception is recorded on
  `AttackAttemptResult.teardown_error` but never flips `succeeded` --
  teardown is best-effort cleanup, not part of the attack's own outcome; a
  successful unlock stays successful even if its cleanup step later fails.

Both hooks are optional callables on `Attack` (default `None`, meaning
"skip"), not required overrides -- most attacks don't need setup/teardown
beyond what their stages already do, and forcing every `Attack` to define
empty no-ops would just be noise.

### Attack selection & class-aware prioritization

`AttackSelector` is a small strategy interface (`selection.py`) rather than
logic baked into the orchestrator, so alternative policies can be swapped in
without touching `AttackOrchestrator`. Two implementations exist:

- **`HighestExpectedSuccessSelector`** (default) -- filters to
  device-compatible attacks and scores each by the product of its stages'
  `expected_success_probability`, tie-broken by fewer stages (less exposure
  on a real device) then by a declared `priority`. Category-blind: an
  `Attack.category` plays no role here beyond whatever `priority` a caller
  happens to assign it.
- **`CategoryAwareSelector`** (opt-in) -- ranks by `Attack.category` first,
  then falls back to the same probability/stages/priority scoring to break
  ties *within* a category. Category *dominates* probability here rather
  than only breaking a tie after it. It is a separate, explicitly-chosen
  selector rather than a change to `AttackOrchestrator`'s own default --
  callers who just want "best odds wins" shouldn't have that silently
  change underneath them.

  Its own bare-constructor default is **`COST_CONSCIOUS_RANK`**
  (`OTHER` > `N_DAY` > `ZERO_DAY`): cheaper, less-exposed attacks are tried
  first, and the more expensive zero-day is only attempted once every
  cheaper candidate has already failed. `ZERO_DAY_FIRST_RANK`
  (`ZERO_DAY` > `N_DAY` > `OTHER`) is also provided for the opposite policy
  -- prefer the zero-day even at somewhat lower odds, since it's less
  likely to already be detected or patched -- e.g. a 40%-odds zero-day
  beating a 90%-odds n-day. Pass either constant, or a custom mapping, via
  `CategoryAwareSelector(category_rank=...)`.

Alternatives considered for the default: a fixed priority list per device
family (simpler, but doesn't adapt when a new attack is added with better
odds than an existing high-priority one), or a cost/time-weighted score
(more realistic, but there's no notion of stage duration modeled here to
weight by).

### Device precondition checks

`DeviceRequirement` (`device.py`) is declarative -- allowed models, min/max
iOS version, min battery -- rather than an arbitrary predicate callable, so
requirements are independently testable and easy to describe without
reading code. iOS versions are compared as padded tuples (`(14,)` behaves
like `(14, 0, 0)` against a longer version), since "iOS 14" and "iOS 14.0.0"
should mean the same precondition. Model/iOS/battery were the fields the
brief called out explicitly; the design leaves room for more (pairing
state, lock state, etc.) without changing the shape of the abstraction.

### What a failed stage means for the rest of the chain

A failed stage **aborts that attack's chain immediately** -- no in-place
retry of the same stage. A real exploit attempt either lands or it doesn't;
retrying it against the same device risks tripping a lockout.

The orchestrator then **falls back to the next-best remaining compatible
attack** by default, rather than giving up outright. This is the richer
answer to "what does a failed stage mean," but it comes with a real caveat
I wanted to be explicit about: repeatedly trying different unlock attacks
against a real device can itself trigger a lockout or wipe. That's exactly
why fallback is bounded by `max_attempts` (default: try every compatible
attack, but easy to cap) rather than being unconditional -- a real
deployment would likely also want backoff/cooldown between attempts, which
is out of scope here but would slot in at the same point.

### Handling a dropped connection mid-chain

Part 2 explicitly calls for a connection that can drop partway through a
chain. `TCPDeviceClient` raises `DeviceDisconnectedError` on any socket
failure or EOF; `AttackOrchestrator` catches it during stage execution,
makes one bounded reconnect attempt (`max_reconnect_attempts`), and retries
the *same* stage once reconnected before falling back to another attack if
reconnecting doesn't work. Whether an attempt needed a reconnect is
recorded on `AttackAttemptResult.reconnected` regardless of whether that
attempt went on to succeed or fail -- a chain that quietly recovered from a
drop and then succeeded is still worth knowing about. (This reconnect
handling is specific to stage execution -- see "attack lifecycle" above for
why `init_attack` treats a disconnect the same as any other setup failure
instead of also retrying.)

### Extraction

Once a chain completes, the orchestrator hands back an `ExtractionSession`
-- a thin capability object wrapping the now-unlocked `DeviceClient`.
`read_file(path)` is a direct pass-through. `extract_all(dest_dir, root)`
recursively walks the device via `list_dir` and mirrors it locally,
downloading each file via `read_file`. A single file's (or subdirectory's)
error is recorded in the returned report and does **not** abort the rest of
the walk -- one unreadable file shouldn't sink an otherwise-successful
extraction.

### Distinguishing expected failures from programmer errors

Several places catch an exception specifically to convert a failure into a
structured result -- a failed `AttackAttemptResult`, a recorded extraction
error, a `DeviceDisconnectedError`. Catching too broadly there is a real
risk: `except Exception` doesn't just catch the failure it's meant to
handle, it also catches a genuine bug in the calling code and silently
mislabels it as an *expected* outcome, burying it instead of surfacing it.
Each of these catches only what it should:

- **`runner.py`** -- `init_attack`/`teardown` are arbitrary user-supplied
  callables, so `TypeError`/`AttributeError`/`NameError` (wrong arity, a
  typo'd attribute) propagate instead of being recorded as "init_attack
  failed." Those mean the *hook itself is broken*, not that it made a
  legitimate decision to fail -- and treating the two the same would hide
  real bugs behind an innocuous-looking failed attempt.
- **`tcp.py`** -- catches `OSError` specifically around socket calls, so a
  bug elsewhere in the client code doesn't get mislabeled as "connection
  lost."
- **`extraction.py`** -- only `OrchestratorError` (and its subclasses),
  `FileNotFoundError`, and `OSError` get recorded as a per-file extraction
  error; anything else propagates as a real bug instead of quietly becoming
  "just another unreadable file" in the report.

Each direction is tested explicitly, not just assumed:
`test_init_attack_bad_arity_propagates_instead_of_being_swallowed` and
`test_teardown_bad_arity_propagates_instead_of_being_swallowed` prove the
bug still surfaces; `test_extract_all_propagates_a_framework_bug_instead_of_recording_it`
proves the same for extraction -- alongside the existing tests proving the
*expected*-failure path still works normally.

### Gating extraction behind a real completed chain

The assignment's wording is precise about this: "once a *chain* completes,"
you get read access -- not "once a stage succeeds." The device itself
enforces this (not just a client-side convention where `ExtractionSession`
is only ever handed out by a successful `run()`), via an explicit `UNLOCK`
command: `AttackOrchestrator` sends it exactly once, the moment a chain's
*every* stage has actually succeeded, right before it hands back the
`ExtractionSession` anyway. `LIST`/`READ` are rejected with
`ERR not_unlocked` until the device has received that signal.

The device can't verify a specific chain's structure on its own -- the wire
protocol has no concept of *attacks* at all, only individual stage
requests, with no way to tell which ones belong to the same chain. So
rather than have the device guess "unlocked" from something partial, like
any single successful `RUN_STAGE` (which would incorrectly leave it
unlocked even if a later stage in that same chain then failed), the one
party that actually knows when a chain finished -- the framework -- just
says so explicitly, instead of the device trying to infer it from
information it was never going to have enough of.

### Protocol: text over binary

Newline-delimited text commands/responses, not length-prefixed binary
framing. Easy to implement and debug in C without a parser library, and
inspectable by hand (`printf 'STATE\n' | nc host port`). The one binary
payload -- a file's raw bytes after `READ` -- is framed by an explicit
length so it's still safe for arbitrary content. The tradeoff is a real
protocol wouldn't necessarily be this readable, but nothing about the
framework depends on the wire format being text; swapping it for a binary
scheme would only touch `protocol.py` and `simulator/src/protocol.c`.

### Simulator concurrency: one connection at a time

The simulator accepts and fully serves one connection before accepting the
next, with blocking I/O -- no `select`/`poll`/threads. This matches the
assignment's actual scope (one orchestrator talking to one device) and
keeps the C side small enough that the interesting logic (stage outcomes,
chaos, protocol parsing) isn't buried in concurrency plumbing the exercise
didn't ask for.

## Protocol

Newline-terminated text lines. The only exception is `READ`'s payload,
which is framed by an explicit length so it can safely contain any bytes
(including newlines) -- everything else is line-oriented.

| Command | Response |
|---|---|
| `STATE` | `STATE model=<m> ios=<v> battery=<n>` |
| `RUN_STAGE <id>` | `OK` or `FAIL <reason>` (simulator rolls its own configured probability, or an override, for `<id>`) |
| `UNLOCK` | `OK` -- sent by the framework once a full chain has completed; grants `LIST`/`READ` (see "gating extraction" below) |
| `LIST <path>` | `OK <n>` then `n` lines of `F <name>` / `D <name>` (direct children only, not recursive), or `ERR <reason>` (`not_found`, or `not_unlocked` before `UNLOCK` has been sent) |
| `READ <path>` | `OK <length>` then exactly `<length>` raw bytes then a trailing `\n`, or `ERR <reason>` (`not_found`, or `not_unlocked` before `UNLOCK` has been sent) |
| `QUIT` | connection closes |

Known simplification: paths and stage ids can't contain spaces (the wire
format tokenizes on whitespace). Fine for this exercise's fixed fixture
data; a real protocol would want quoting or length-prefixing for paths too.

## Building and running

### Python framework (Part 1 only, no simulator needed)

```bash
python -m pip install pytest
python -m pytest tests/unit
```

Run from the repo root. `tests/unit` uses `FakeDeviceClient` only -- no
build step, no network, no C compiler required. (There's no `pip install`
step for the `orchestrator` package itself -- the root `conftest.py` puts
`framework/` on `sys.path` for you.)

Use `python -m pytest` rather than a bare `pytest` -- `pip install`'s
scripts don't always land on `PATH` (this is the normal case on WSL, where
they go to `~/.local/bin`), so `python -m pytest` is the form that's
actually guaranteed to work regardless of environment. On WSL/Linux that's
`python3 -m pytest`.

### C simulator (Part 2) -- needs a POSIX environment (WSL or Linux)

```bash
cd simulator
make
./simulator --port 9000
```

Useful flags for scripting deterministic scenarios (see integration tests
for real examples):

```bash
./simulator --port 9000 --force pair=fail --force fast_pair=success
./simulator --port 9000 --drop-after 2   # drops the connection on the 2nd command received
./simulator --port 9000 --seed 7         # reseed the RNG used for un-forced stages
./simulator --port 9000 --model iPhone8  # override the reported model (default "iPhone12")
```

### Full demo (Part 1 + Part 2 together)

`run_demo.py` defines four example attacks with deliberately different
shapes and categories, specifically to exercise selection rather than just
running one fixed thing: `fast_pair_unlock` (a short, high-odds 2-stage
n-day chain, restricted to iOS 15+), `full_bypass_chain` (a longer 3-stage
n-day chain with a wider device requirement), `brute_force_pin` (a single
low-odds "other"-category stage), and `zero_day_unlock` (a single
very-high-odds stage, categorized as `ZERO_DAY`). It runs them through
`AttackOrchestrator(build_attacks(), selector=CategoryAwareSelector())` --
not the default selector -- specifically so the demo also shows
category-aware selection in action: under `CategoryAwareSelector()`'s
cost-conscious default, the "other"-category `brute_force_pin` gets picked
over the far-higher-odds `zero_day_unlock`, since category is ranked above
raw probability and the expensive zero-day is only meant to be reached for
once the cheaper candidates have failed. Running it walks through the full
query -> select -> run -> extract flow end-to-end against the simulator's
fixture device.

```bash
# terminal 1, under WSL/Linux
cd simulator && make && ./simulator --port 9000

# terminal 2, from the repo root
python framework/examples/run_demo.py --host 127.0.0.1 --port 9000

# --dest controls where extracted files are written (default: ./extracted/)
python framework/examples/run_demo.py --host 127.0.0.1 --port 9000 --dest /tmp/loot

# add -v/--verbose to stream real-time orchestrator log output
# (device state queried, attack selected, each stage's outcome, unlock,
# extraction summary) as the run happens, on top of the final summary print
python framework/examples/run_demo.py --host 127.0.0.1 --port 9000 -v
```

The orchestrator (`runner.py`) and extraction (`extraction.py`) modules log
through the standard `logging` module at `INFO` level and stay silent unless
a caller configures a handler -- `-v` on the demo CLI just does
`logging.basicConfig(level=logging.INFO)`. This is opt-in real-time
visibility, distinct from `AttackRunResult`/`ExtractionReport`, which stay
the structured, testable source of truth for the final outcome.

### Integration tests (build + drive the real simulator)

```bash
python3 -m pytest tests/integration   # requires gcc/make on PATH; builds simulator/ automatically
```

This only actually runs anything under WSL/Linux, since building the
simulator needs `gcc`/`make`. Run everything (unit + integration) with
`python3 -m pytest` from the repo root under WSL/Linux; on plain Windows,
`python -m pytest` will still collect the integration tests but they'll
skip themselves (rather than failing the run) once they notice `gcc`/`make`
aren't on `PATH`.

## Testing strategy

**Unit** (`tests/unit`, `FakeDeviceClient`, no build/network): attack
selection among multiple/zero compatible candidates and its tie-breaks
(including `CategoryAwareSelector`'s `COST_CONSCIOUS_RANK` default only
reaching for the zero-day once every cheaper candidate has failed); *and*
`AttackOrchestrator` actually using an injected non-default selector
end-to-end, not just the selector's own scoring logic in isolation; a
genuine multi-stage chain where an earlier stage succeeds before a later
one fails, proving the abort happens at the actual failing stage (not the
first one) and that no stage past it ever runs; stage-failure
driving fallback to the next attack, including `max_attempts`; the full
disconnect -> reconnect -> continue path *and* the disconnect ->
reconnect-fails -> fallback path; `init_attack`/`teardown` lifecycle hooks
(init failure driving fallback, teardown running on both outcomes, teardown
errors being recorded without flipping success); that a successful chain
calls `unlock()` and a failed one never does; extraction's
nested-directory walk, its per-file error tolerance, *and* its own
reconnect-and-retry on a dropped connection mid-walk (both the
recovers-and-continues case and the reconnect-fails-so-it's-recorded-as-an-
error case); `TCPDeviceClient.close()`'s best-effort `QUIT` behavior,
unit-tested directly against a stand-in link rather than only ever through
the real simulator; the bug-vs-expected-failure propagation described above
(`init_attack`/`teardown` bad-arity and the extraction equivalent);
`protocol.py`'s encode/decode round-trips in isolation from
any socket; `Stage`'s probability-range validation and `Attack`'s
empty-stage-list validation.

**Integration** (`tests/integration`, real compiled simulator, real TCP):
`STATE` parsing against the real fixture device; the `not_unlocked` gate
actually being enforced by the simulator itself (not just assumed
client-side) both before any stage has run *and* after a stage succeeds but
before the explicit `UNLOCK` is sent -- proving the gate really is keyed on
"a chain completed," not "some stage passed" -- with `LIST` given its own
direct test of the same gate rather than only inferring it from `READ`'s;
a full successful run plus
`extract_all` against real files served by the real C process; a genuine
three-stage chain run end-to-end over real, sequential `RUN_STAGE`
round-trips (every other integration test uses a single-stage attack, so
this is the one that actually proves multi-stage execution against the
real simulator, not just the framework's internal bookkeeping); a
`--force`-driven stage failure actually producing the real fallback path;
device-model filtering against a real device that reports a different
model via `--model` (not just synthetic `DeviceState` objects), proving an
incompatible attack is skipped and a compatible one runs and extracts
end-to-end; and the mid-chain-disconnect scenario against a real dropped
socket (via `--drop-after`), not a mocked one -- this is the one that most
directly answers Part 2's "connection that drops partway through a chain."
A separate group of tests reaches past `TCPDeviceClient`'s normal command
methods to send `protocol.c`'s parser raw malformed input directly (an
empty line, a command missing its required argument, a line far past the
fixed-size line buffer), proving it degrades gracefully -- a clean `ERR`
response, connection still usable afterward -- rather than crashing or
hanging, even though no legitimate framework usage would ever trigger
these on its own.
