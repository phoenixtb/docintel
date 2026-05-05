# 01 — Proxmox + Hardware Orchestration

Target: turn the dedicated machine into a 24×7 model-serving host that can be flexibly re-allocated between Ubuntu (production) and Windows (testing) workloads, while keeping always-on lightweight services in LXCs.

## 1. Hardware inventory

| Component | Spec | Use |
|---|---|---|
| CPU | Intel Core Ultra 7 (Arrow Lake / Meteor Lake) | Host + VMs |
| iGPU | Intel Arc Graphics (Xe-LPG) | Always available to host & LXCs (OpenVINO-capable) |
| dGPU | NVIDIA RTX 5060, 16 GB VRAM | Switchable: Ubuntu VM (default) or Windows VM (testing) |
| RAM | 32 GB DDR5 | 24 GB → Ubuntu VM, 8 GB → host + LXCs (or 20 / 12 split) |
| Storage | (TBD) | Recommend ZFS mirror or single NVMe with regular backups |
| OS | Proxmox VE 8.x (Debian 12 base) | Hypervisor |

## 2. Final topology

```
┌──────────────────────────────────────────────────────────────────┐
│ Proxmox VE host                                                  │
│  • iGPU (Intel Arc) stays with host  → shared to LXCs            │
│  • dGPU (RTX 5060) bound to vfio-pci → switchable to one VM      │
│                                                                  │
│  ┌───────────────────────┐    ┌───────────────────────┐          │
│  │ VM: ubuntu-ml (24×7)  │    │ VM: win-test (on-demand)│        │
│  │  • RTX 5060 (when on) │ XOR│  • RTX 5060 (when on)  │         │
│  │  • 24 GB RAM          │    │  • 24 GB RAM           │         │
│  │  • Docker + LMForge   │    │  • LMForge + DocIntel  │         │
│  │  • DocIntel services  │    │    Windows install     │         │
│  └───────────────────────┘    └───────────────────────┘          │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ LXCs (always-on, share host iGPU via /dev passthrough)│       │
│  │  • lxc-ovino : OpenVINO inference (optional Phase 2)  │       │
│  │  • lxc-misc  : monitoring, mDNS, reverse proxy        │       │
│  └──────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────┘
                       ▲
                       │ LAN (gigabit+, mDNS or static)
                       │
   DocIntel main host (your Mac), mobile apps, other clients
```

Key invariants:
- **dGPU is a single-tenant resource at any moment.** Mutual exclusion enforced by `vfio-pci` ownership; a VM cannot start with the device while another VM holds it. Switching = shutdown source VM → start target VM.
- **iGPU is always shared with host.** LXCs get `/dev/dri/*` passthrough; no driver conflict with the dGPU.

## 3. BIOS / UEFI prerequisites

Before Proxmox install, in firmware:

| Setting | Value | Why |
|---|---|---|
| Intel VT-x | Enabled | CPU virtualization |
| Intel VT-d (IOMMU) | Enabled | PCI passthrough (mandatory) |
| Above 4G Decoding | Enabled | Required for modern NVIDIA GPUs |
| Re-Size BAR Support | Enabled | RTX 5060 ReBAR for higher throughput |
| Secure Boot | Disabled | Required for vfio-pci on some boards (revisit after install) |
| CSM | Disabled | Pure UEFI mode |
| Resizable BAR | Enabled | NVIDIA requirement |
| iGPU Multi-Monitor | Enabled | Keeps iGPU active even with dGPU present |
| Primary Display | iGPU | So Proxmox console uses iGPU, leaving dGPU free for VFIO |

If SR-IOV options exist for the iGPU, **leave disabled for now** (the simple `/dev/dri` passthrough we're using doesn't need it).

## 4. Proxmox VE install

1. Download Proxmox VE 8.x ISO from `proxmox.com/downloads`.
2. Flash to USB with Rufus / `dd`.
3. Boot installer; choose:
   - Filesystem: **ZFS RAID0** (single NVMe) or **ZFS RAID1** (mirror) — gives you snapshots and replication later.
   - Hostname: `proxmox.lan` (or your domain)
   - Static IP for the host on your LAN
4. After first boot, log into web UI at `https://<host-ip>:8006`.
5. Disable enterprise repo, enable no-subscription repo:
   ```bash
   # /etc/apt/sources.list.d/pve-no-subscription.list
   deb http://download.proxmox.com/debian/pve bookworm pve-no-subscription
   ```
   Comment out the enterprise repo in `/etc/apt/sources.list.d/pve-enterprise.list`.
6. `apt update && apt full-upgrade && reboot`.

## 5. IOMMU + VFIO setup (required for dGPU passthrough)

### 5.1 Enable IOMMU on the kernel cmdline

Edit `/etc/default/grub`:
```bash
GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"
```

Apply and reboot:
```bash
update-grub
reboot
```

Verify:
```bash
dmesg | grep -e DMAR -e IOMMU
# Should see: "DMAR: IOMMU enabled"

find /sys/kernel/iommu_groups/ -type l | sort -V
# Should list devices grouped by IOMMU
```

### 5.2 Identify the dGPU's PCI IDs

```bash
lspci -nn | grep -iE 'nvidia|vga'
# Example output:
# 01:00.0 VGA compatible controller [0300]: NVIDIA Corporation Device [10de:2860]
# 01:00.1 Audio device [0403]: NVIDIA Corporation Device [10de:22bd]
```

Note both IDs (`10de:2860` and `10de:22bd`) — the GPU and its companion HDMI audio device are always passed together.

### 5.3 Bind the dGPU to vfio-pci at boot

Create `/etc/modprobe.d/vfio.conf`:
```
options vfio-pci ids=10de:2860,10de:22bd disable_vga=1
softdep nvidia pre: vfio-pci
softdep nouveau pre: vfio-pci
```

Blacklist NVIDIA host drivers in `/etc/modprobe.d/blacklist.conf`:
```
blacklist nouveau
blacklist nvidia
blacklist nvidiafb
blacklist nvidia_drm
```

Add VFIO modules in `/etc/modules`:
```
vfio
vfio_iommu_type1
vfio_pci
vfio_virqfd
```

Apply:
```bash
update-initramfs -u -k all
reboot
```

Verify after reboot:
```bash
lspci -nnk -s 01:00
# Should show:
# Kernel driver in use: vfio-pci
```

### 5.4 IOMMU group sanity check

```bash
for d in /sys/kernel/iommu_groups/*/devices/*; do
  n=${d#*/iommu_groups/*}; n=${n%%/*}
  printf 'IOMMU Group %s ' "$n"
  lspci -nns "${d##*/}"
done | grep -i nvidia
```

The RTX 5060 (and its audio device) must be in their own IOMMU group with nothing else important. If there's bleed (e.g., a SATA controller in the same group), enable **PCIe ACS Override** patch — but try without first; recent Intel chipsets usually have clean groups.

## 6. Storage layout

ZFS dataset plan (assumes pool named `rpool`):

```
rpool/data            (Proxmox default — VM disks)
rpool/data/iso        (ISO images)
rpool/data/lxc        (LXC root filesystems)
rpool/data/models     (HF / GGUF model store, mounted into LMForge LXC/VM)
rpool/data/backup     (vzdump targets)
```

Create the model store as a shared dataset:
```bash
zfs create -o compression=lz4 rpool/data/models
zfs set quota=200G rpool/data/models   # or whatever you allocate
```

Mount inside Ubuntu VM via virtio-9p or virtfs (faster than NFS for local). Alternative: keep model store inside the VM disk and back up the whole VM. Simpler unless you need cross-VM model sharing.

## 7. The Ubuntu ML VM (`ubuntu-ml`, 24×7)

### 7.1 Create the VM in Proxmox UI

| Setting | Value |
|---|---|
| Node | proxmox |
| VM ID | 100 |
| Name | ubuntu-ml |
| OS | Ubuntu 24.04 Server (cloud image preferred) |
| Machine | q35 |
| BIOS | OVMF (UEFI) — required for GPU passthrough |
| EFI disk | Add (rpool/data, 4 MB) |
| SCSI controller | VirtIO SCSI single |
| Disk | 100 GB on rpool/data, discard=on, ssd=on |
| CPU | host, 8 cores, NUMA on |
| Memory | 24576 MB, ballooning OFF (required for passthrough) |
| Network | virtio, vmbr0 (bridged to LAN), MAC reserved |

### 7.2 Add the dGPU to the VM

In Proxmox UI → VM → Hardware → Add → PCI Device:
- Device: `0000:01:00` (parent — adds both functions)
- All Functions: ✓
- Primary GPU: ✗ (keep host console on iGPU)
- ROM-Bar: ✓
- PCI-Express: ✓

This generates in `/etc/pve/qemu-server/100.conf`:
```
hostpci0: 0000:01:00,pcie=1,x-vga=0
```

### 7.3 First boot, install OS

- Use `cloud-init` user data for static network, SSH key, hostname.
- `apt update && apt install -y nvidia-driver-560 nvidia-utils-560` (or current stable).
- `nvidia-smi` should show the RTX 5060.
- Install Docker:
  ```bash
  curl -fsSL https://get.docker.com | sh
  apt install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
  docker run --rm --gpus all nvidia/cuda:12.5-base-ubuntu22.04 nvidia-smi
  ```
- Install LMForge: see Doc 02.

### 7.4 Auto-start at host boot

In VM options → Start at boot: ✓, Start/Shutdown order: 1.

## 8. The Windows test VM (`win-test`, on-demand)

Same recipe as Ubuntu VM with these differences:

| Setting | Value |
|---|---|
| VM ID | 101 |
| OS | Windows 11 |
| Machine | q35 |
| BIOS | OVMF + add TPM v2.0 disk (Windows 11 requirement) |
| Disk | 120 GB on rpool/data |
| CPU | host, 8 cores |
| Memory | 16384 MB (less than Ubuntu — testing only) |
| Network | virtio (install drivers from virtio-win.iso during setup) |
| Start at boot | ✗ (manual only) |

Add the dGPU PCI device the same way — but do **not** start `win-test` while `ubuntu-ml` is running. The vfio-pci will refuse to bind twice.

### 8.1 Switching procedure (manual)

```bash
# To test on Windows:
qm shutdown 100        # graceful Ubuntu shutdown — waits for guest
qm wait 100 --timeout 120
qm start 101           # start Windows; dGPU reattaches

# Back to production:
qm shutdown 101
qm wait 101 --timeout 120
qm start 100
```

### 8.2 Optional: hookscript for auto-rotation

`/var/lib/vz/snippets/gpu-rotate.sh` (chmod +x) — if you want a "switch GPU" command that one-shot does both:

```bash
#!/usr/bin/env bash
# Usage: /var/lib/vz/snippets/gpu-rotate.sh ubuntu | windows
set -euo pipefail
case "$1" in
  ubuntu)  qm shutdown 101 || true; qm wait 101 --timeout 120 || true; qm start 100 ;;
  windows) qm shutdown 100 || true; qm wait 100 --timeout 120 || true; qm start 101 ;;
  *) echo "usage: $0 ubuntu|windows" ; exit 1 ;;
esac
```

Symlink to `/usr/local/bin/gpu-switch`.

## 9. iGPU passthrough to LXCs (always-on lightweight workloads)

The Intel Arc iGPU exposes `/dev/dri/card0` and `/dev/dri/renderD128` on the host. We pass these into LXC containers so OpenVINO / VA-API / iHD can use the iGPU without a full VM.

### 9.1 Verify iGPU on host

```bash
ls -l /dev/dri/
# crw-rw---- 1 root video 226,   0 ...  card0
# crw-rw---- 1 root render 226, 128 ...  renderD128

apt install -y intel-gpu-tools
intel_gpu_top
```

### 9.2 Create an LXC for OpenVINO (Phase 2 — optional)

Container config (e.g., `/etc/pve/lxc/200.conf`):

```
arch: amd64
cores: 4
hostname: lxc-ovino
memory: 4096
ostype: ubuntu
rootfs: local-zfs:subvol-200-disk-0,size=20G
swap: 2048
unprivileged: 1
features: nesting=1

# Pass iGPU devices in
lxc.cgroup2.devices.allow: c 226:0 rwm
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri/card0 dev/dri/card0 none bind,optional,create=file
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file

# (Inside the LXC, ensure the user is in the 'render' and 'video' groups,
#  matching the host GIDs. For unprivileged LXCs this requires id-mapping
#  via /etc/pve/lxc/200.conf "lxc.idmap" lines — see Proxmox wiki.)
```

Inside the LXC:
```bash
apt install -y intel-opencl-icd intel-media-va-driver-non-free clinfo
clinfo | grep -i intel        # confirms OpenCL on iGPU
vainfo                          # confirms VA-API
# Then install OpenVINO runtime if needed
```

**Decision:** Mark this LXC as Phase 2. Don't build it on day 1 unless you have a concrete OpenVINO workload. The Ubuntu VM with the dGPU will handle 100% of LMForge's load.

### 9.3 Always-on services LXC (recommended for day 1)

Create a small LXC `lxc-misc` (no GPU) for things you want decoupled from the VMs:

- mDNS responder (`avahi-daemon`) so LAN devices can find `ubuntu-ml.local`
- Reverse proxy (`caddy` or `traefik`) terminating TLS for LMForge if you want HTTPS
- Prometheus node-exporter pointed at the host
- Backup orchestrator (rclone to remote)

This keeps the Ubuntu VM focused on serving models.

## 10. Networking

### 10.1 Bridge layout

`/etc/network/interfaces` on the host (Proxmox installer sets this up):

```
auto lo
iface lo inet loopback

auto eno1
iface eno1 inet manual

auto vmbr0
iface vmbr0 inet static
    address 192.168.1.10/24
    gateway 192.168.1.1
    bridge-ports eno1
    bridge-stp off
    bridge-fd 0
```

VMs and LXCs all attach to `vmbr0` and get their own LAN IPs (DHCP reservations recommended).

### 10.2 Reserved IPs

| Host | IP | Hostname |
|---|---|---|
| proxmox | 192.168.1.10 | proxmox.lan |
| ubuntu-ml | 192.168.1.20 | ubuntu-ml.lan |
| win-test | 192.168.1.21 | win-test.lan |
| lxc-misc | 192.168.1.30 | lxc-misc.lan |
| lxc-ovino | 192.168.1.31 | lxc-ovino.lan |

DocIntel main host on your Mac will hit `http://ubuntu-ml.lan:11430/v1` (or the static IP).

### 10.3 Firewall

On the Ubuntu VM (`ufw`):
```bash
ufw allow from 192.168.1.0/24 to any port 11430 proto tcp   # LMForge
ufw allow from 192.168.1.0/24 to any port 22    proto tcp   # SSH
ufw enable
```

LMForge listens on `127.0.0.1:11430` by default — change to `0.0.0.0:11430` (Doc 02 covers this) so other LAN devices can reach it.

## 11. Backup & disaster recovery

- Proxmox `vzdump` daily for `ubuntu-ml` to `rpool/data/backup`, retain 7.
- Weekly `vzdump` for `win-test`, retain 2.
- Replicate `rpool/data/backup` off-box (rclone to S3 / NAS / external SSD).
- Snapshot ZFS dataset `rpool/data/models` daily — quick restore if a model corrupts.

## 12. Verification checklist

After full setup:

- [ ] Host: `lspci -nnk -s 01:00` shows `Kernel driver in use: vfio-pci`
- [ ] Host: `intel_gpu_top` works on iGPU
- [ ] Ubuntu VM: `nvidia-smi` shows RTX 5060, 16 GB VRAM
- [ ] Ubuntu VM: `docker run --rm --gpus all nvidia/cuda:12.5-base nvidia-smi` works
- [ ] Ubuntu VM: LMForge daemon serves on `0.0.0.0:11430`, reachable from Mac
- [ ] Switching: shutdown Ubuntu → start Windows → `nvidia-smi` works in Windows → reverse
- [ ] LXC (if built): `vainfo` and `clinfo` show iGPU
- [ ] mDNS: `ping ubuntu-ml.lan` resolves from your Mac

## 13. Open decisions to make before starting

1. **Single NVMe vs mirror?** Mirror gives you peace of mind but doubles drive cost.
2. **Static IPs at the router (DHCP reservation) or static config in each VM?** Reservations are cleaner.
3. **TLS for LMForge?** Probably overkill on a private LAN, but if you ever expose via Tailscale / WireGuard, terminate at `lxc-misc` Caddy.
4. **Will any other devices in your house need to reach LMForge?** If yes, bind to `0.0.0.0` with firewall whitelisting; if Mac-only, you can keep it `127.0.0.1` and SSH-tunnel.

---

Done with Doc 1. Next: Doc 2 covers the LMForge changes needed to serve VLMs over the same daemon.
