# Failures: symptom, cause, fix

Every entry here cost real time on this machine. Match on the symptom text.

### `request (N tokens) exceeds the available context size (M tokens), try increasing it.`
The client sent more history than the server can hold. Either raise `-c`, or teach the client to
trim earlier. The subtle bug is a client that trims at `1.5 * cap`: with a 20,480 wall it waited for
30,720 and could never fire in time. Trim at `cap - cap/4` so the server limit always exceeds what
the client allows itself.

### `slot release: n_tokens = 12287, truncated = 1` followed by `500 Failed to parse tool call arguments as JSON ... missing closing quote`
A tool call was cut mid-JSON by the context wall, so the tool arguments were not parseable and the
poisoned turn was re-sent forever. Three fixes, all worth having: raise context, repair the transcript
client-side (replace the invalid tool call with a valid JSON error carrying the recovered prefix, and
keep the paired result), and surface the server's body instead of `curl: (22) error 500`.

### `curl: (28) Operation timed out after 180000 milliseconds with 994065 bytes received`
A total-transfer timeout, not a stall. A healthy long generation was being killed solely for
length. Use a silence budget instead: `--speed-limit 1 --speed-time 180` with a generous
`--max-time 1440` and `--connect-timeout 30`. Verified against a deliberately dribbling server: a
24 second stream survives a 5 second stall budget, a 15 second silence dies with `Operation too
slow` rather than hanging.

### Server answers with empty `content` and `finish_reason: length`
The generation budget was consumed by reasoning. A 900-token cap produced 3,731 characters of
reasoning and nothing else. Raise the cap to at least 1,500 and preferably 3,000 to 8,000, or add a
short "think briefly, then answer" instruction to the system prompt.

### `--reasoning off` is set but `content` is still empty
DeepSeek-family models put text in `reasoning_content`. Add `--reasoning-format none`. Both flags.

### Model loads but runs at a tenth of expected speed
Not a model problem, a fit problem. Check in this order: other model servers holding VRAM
(`pgrep -a llama-server`), VRAM at the ceiling with GTT climbing (spill), more threads than physical
cores, and a browser or compositor eating CPU.

### `failed to fit params to free device memory: n_gpu_layers already set by user to N, abort`
The server refused to place layers and loaded anyway, forcing the rest to CPU. The model is running
on the host. Free VRAM, or lower `-ngl`/raise `-ncmoe` until it fits honestly.

### Prefill is absurdly slow but decode is fine
Usually mmap plus tensor overrides. Page faults inside prompt processing. Switch to `--load-mode
none` if RAM allows. Measured 48.7 to 218.6 t/s.

### Decode collapses from ~10 t/s to ~2 t/s with no error
Silent VRAM spill. Compare `mem_info_vram_used` to `mem_info_vram_total`. Back off `-ncmoe` by 4 or
shrink `-c`/`-ub`.

### `failed to set thread priority`
`--prio` without privilege. On this box it emits one line per thread, tens of thousands per run, and
slows everything. Remove the flag.

### Long generations die at almost exactly two minutes
`--timeout` default. Set `--timeout 1800` in the unit.

### WebGL2 blocklisted / `new THREE.WebGLRenderer()` throws
Not llama.cpp, but the same class of problem: the desktop renderer falls back to llvmpipe and eats
cores the model needs. Either close the tab before measuring, or launch Chrome with
`--ignore-gpu-blocklist --enable-unsafe-swiftshader` when it has to stay open.

### `pkill -f llama-server` kills the calling shell
Pattern matching sees the agent's own wrapper command line, which contains the pattern. Kill by PID
from `/proc/<pid>/cmdline`, or stop the systemd unit by name. Symptom of the mistake: exit code -9
that looks like an out-of-memory kill.

### A `-j6` build or a model vanishes with exit 143
The agent's terminal tool puts background work in a scope with a 4 GB memory ceiling, and the kernel
OOM-kills past it. Launch heavy servers and builds inside
`systemd-run --user --scope -p MemoryMax=22G`. Check `dmesg | grep CONSTRAINT_MEMCG` to confirm.

### GGUF reported as corrupt by a hand-rolled parser
GGUF v3 header counts are uint64, not uint32. Use `llama-gguf` or gguf-py. Also check the magic bytes
(`head -c 4 file | xxd` should show `47475546`).

### `sha256sum -c` passes but the model behaves unlike its card
A checksum proves a faithful copy of what the repo published, not that the repo matches the card. For
new architectures, read the live `config.json` and README from the repo, not from your training
cutoff.

### Second model loads alongside the first and both are slow
8 GB does not divide. Stop the first server, verify ~90 MB VRAM, then start the second.
