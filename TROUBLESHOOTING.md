# Troubleshooting Guide

## GPU Out of Memory (OOM) Errors

### Error: "Allocation on device 0 would exceed allowed memory"

This means your GPU ran out of VRAM. **Most common cause: batch_size is set too high.**

#### Immediate Fix: Check Batch Size

1. **In ComfyUI, check ALL nodes for `batch_size` parameter:**
   - `Empty Latent Image` node → `batch_size` should be `1` (not 2, 4, or higher)
   - `CLIPTextEncode` nodes → No batch_size parameter (should be automatic)
   - Any other nodes with batch_size → Set to `1`

2. **Clear GPU memory:**
   ```bash
   ./app.sh restart
   ```

3. **Verify image size:**
   - `Empty Latent Image` → `width: 512`, `height: 512` (not 1024)

4. **Try again** with batch_size = 1

#### If Still Getting OOM

1. **Reduce image size further:**
   - Try `384x384` or `448x448` instead of `512x512`

2. **Reduce steps:**
   - `KSampler` → `steps: 10` (instead of 15-20)

3. **Close browser tabs** and other GPU applications

4. **Check GPU memory usage:**
   ```bash
   watch -n 1 nvidia-smi
   ```
   Watch for memory usage > 3.5GB (your limit is ~3.68GB)

---

## System Restarts When Running ComfyUI

If your system restarts automatically when you click "Queue Prompt" in ComfyUI, this is usually a **hardware power/thermal issue**, especially on laptops.

### Immediate Fixes (Try These First)

#### 1. Reduce Image Size in ComfyUI
**This is the most important fix for RTX 3050 (4GB VRAM):**

- In your `Empty Latent Image` node:
  - Change `width` from `1024` to `512`
  - Change `height` from `1024` to `512`
  - **512x512 uses ~75% less VRAM than 1024x1024**

#### 2. Reduce Sampling Steps
- In your `KSampler` node:
  - Change `steps` from `20` to `15` or `10`
  - Fewer steps = less GPU work = less power draw

#### 3. Use Lower Precision (if available)
- Some ComfyUI workflows support `fp16` (half precision)
- This reduces VRAM usage and power draw

#### 4. Close Other Applications
- Close browser tabs, other GPU apps
- Free up system RAM and GPU memory

### Laptop-Specific Fixes

#### 1. Set Performance Mode
```bash
# Check current power mode
cat /sys/firmware/acpi/platform_profile

# Set to performance (if available)
echo performance | sudo tee /sys/firmware/acpi/platform_profile
```

#### 2. Reduce GPU Power Limit (Temporary)
```bash
# Check current power limit
nvidia-smi -q -d POWER

# Reduce power limit to 80% (example: if max is 80W, set to 64W)
sudo nvidia-smi -pl 64
```

**Warning:** This will reduce performance but prevent crashes.

#### 3. Enable Laptop Cooling
- Use a laptop cooling pad
- Ensure vents are not blocked
- Clean dust from fans

#### 4. Check Power Adapter
- Ensure you're using the **original power adapter**
- Laptops often throttle on battery or weak adapters
- Some laptops require >100W adapters for full GPU performance

### Monitor GPU During Generation

Open a terminal and run:
```bash
watch -n 1 nvidia-smi
```

Watch for:
- **Temperature > 85°C** → thermal throttling risk
- **Power draw near limit** → power throttling risk
- **Memory usage > 90%** → out of memory risk

### Alternative: Use CPU Mode (Slower but Stable)

If GPU keeps crashing, you can run ComfyUI in CPU mode:

1. Edit `docker-compose.yml`:
   ```yaml
   comfyui:
     # Comment out GPU access temporarily
     # deploy:
     #   resources:
     #     reservations:
     #       devices:
     #         - driver: nvidia
     #           count: all
     #           capabilities: [gpu]
   ```

2. Restart services:
   ```bash
   ./app.sh restart
   ```

**Note:** CPU mode is 10-50x slower but won't crash your system.

### Check System Logs After Crash

After a restart, check what caused it:

```bash
# Check system logs
sudo journalctl -b -1 --priority=err | tail -50

# Check kernel messages
sudo dmesg | grep -i -E "(error|crash|panic|gpu|nvidia|thermal|power)"

# Run diagnostic script
./scripts/diagnose_system_crash.sh
```

### Recommended ComfyUI Settings for RTX 3050 (4GB)

For stable operation on RTX 3050 Laptop:

- **Image Size:** 512x512 (max 768x768)
- **Steps:** 15-20
- **CFG Scale:** 7-8
- **Batch Size:** 1 (never use batch > 1)
- **Model:** SD 1.5 (not SD 2.x or SDXL)

### Still Crashing?

1. **Update NVIDIA drivers:**
   ```bash
   sudo apt update
   sudo apt install --upgrade nvidia-driver-580
   sudo reboot
   ```

2. **Check BIOS settings:**
   - Disable "Hybrid Graphics" if available
   - Set GPU to "Discrete" mode
   - Disable power saving features

3. **Contact support:**
   - Check laptop manufacturer forums
   - Some laptops have known GPU power issues
   - May need firmware/BIOS update

---

## Other Common Issues

### ComfyUI Not Accessible

**Symptoms:** Can't connect to http://localhost:8188

**Fix:**
```bash
# Check if container is running
docker ps | grep comfyui

# Check logs
docker logs comfyui

# Restart service
./app.sh restart
```

### Out of Memory Errors

**Symptoms:** `CUDA out of memory` errors

**Fix:**
- Reduce image size to 512x512
- Reduce batch size to 1
- Close other applications
- Use SD 1.5 instead of SDXL

### Slow Generation

**Symptoms:** Images take >60 seconds to generate

**Normal for:**
- RTX 3050: 15-30 seconds per 512x512 image
- CPU mode: 5-10 minutes per image

**To speed up:**
- Use GPU (not CPU)
- Reduce steps (15 instead of 20)
- Use smaller images (512x512)
- Close other applications

---

## Diagnostic Commands

```bash
# Full system diagnostic
./scripts/diagnose_system_crash.sh

# GPU status
nvidia-smi

# Docker GPU test
./scripts/check_gpu_docker.sh

# Service status
./app.sh status

# View logs
./app.sh logs
```
