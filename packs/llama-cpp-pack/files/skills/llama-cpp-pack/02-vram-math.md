# VRAM math, and how to prove there is no spill

## The budget

```
VRAM needed = weights_on_gpu + KV_cache + compute_buffers + vision_projector + framebuffer
```

Rules of thumb for an 8 GB card that also drives a desktop, so about 7.6 GB usable:

- **Weights**: for a MoE, roughly `file_size * (layers_on_gpu / total_layers)` for the expert part
  plus all attention and embedding layers. A 40-layer 18.24 GiB Q4_0 model at `-ncmoe 26` put 14
  layers of experts on the card and measured 8.05 GB used, so ~0.32 GB of expert weights per layer.
- **KV cache**: `2 * n_layers_kv * n_kv_heads * head_dim * ctx * bytes_per_element`. Halve for q8_0,
  quarter for q4_0. Hybrid models (gated delta net, linear attention) keep O(1) state on most layers
  and only pay full KV on the attention layers, which is why a 35B hybrid at 32k ctx fits where a
  dense 27B at 16k does not.
- **Compute buffers**: 0.3 to 1.0 GB depending on `-ub`. Shrinking `-ub` is the cheapest way to
  recover 200-400 MB.
- **Framebuffer/desktop**: 80 to 200 MB on this box with Xorg running.

## Measure, do not estimate

```bash
# Amdgpu exposes both heaps.
cat /sys/class/drm/card1/device/mem_info_vram_used   # what the card holds
cat /sys/class/drm/card1/device/mem_info_vram_total  # the ceiling
cat /sys/class/drm/card1/device/mem_info_gtt_used    # system RAM the GPU can touch
```

**Reading GTT correctly.** GTT is not automatically spill. On this box a model with most experts in
RAM shows 12 to 16 GB of GTT because the CPU-side weights are mapped into the GPU's address space.
The tell for real spill is **VRAM at or above physical total while GTT also climbs**, plus a decode
rate that collapses (10.29 to 2.20 t/s in one step). A 34-layer offload showed 4.44 GB VRAM and 15.8
GB GTT and ran fine; a 22-layer offload showed 8.53 GB VRAM and 2.20 t/s.

**The other tell is the server's own load log.** `failed to fit params to free device memory:
n_gpu_layers already set by user to N, abort` means it loaded anyway, with layers forced to CPU. The
same model then reports `MiB free` around 2500 instead of 8085 at load time.

## RAM is part of the budget

Models above VRAM live partly in system RAM, and the kernel page cache is what makes that tolerable.
Two consequences:

- **`--load-mode none`** needs the full model in RAM at once. An 18.24 GiB file plus a desktop plus
  page cache does not fit in 31 GB with much room left, so check before choosing it.
- **Two large models cannot coexist.** 31 GB RAM minus one 18 GiB model leaves no room for a second.
  This is why "one model per card" is also "one model per box".

```bash
free -g                     # available, not free: page cache counts
grep '^Cached' /proc/meminfo
df -h /                     # weights for a run belong on the SATA root disk, not a USB drive
```

USB-attached model drives run at 15 to 66 MB/s on this box. A 19 GB model read from USB during prefill
turns a 6 second prefill into minutes. Copy the weights you are about to measure to the internal SSD
first; keep the cold library on USB.

## Sizing a card for a model, in one pass

1. Read the file size from `ls -l`.
2. `-ngl 999` and all experts on CPU: `-ncmoe <layer_count>`. Measure VRAM.
3. Drop `-ncmoe` by 4 until VRAM is within ~300 MB of physical total, then stop. Below that, decode
   collapses rather than degrading.
4. Add `-ctk q8_0 -ctv q8_0`, then raise `-c` until VRAM is back at the ceiling.
5. Only then tune `-t`, `-tb`, `-b`, `-ub` and sampling.

Every step is a restart, so keep the probe script ready and make the restart cheap: one systemd unit,
one log file, one health poll.
