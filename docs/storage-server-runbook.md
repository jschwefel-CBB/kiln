# Storage Server — Build Runbook (TrueNAS, zero prior experience)

This is the click-by-click guide to building kiln's storage server from bare
hardware to two working, mounted archive pools. It assumes you have **never used
TrueNAS before**. Every screen and button is named exactly as it appears.

For *why* the hardware and pool layout are the way they are — the drive
inventory, the RAIDZ2/RAIDZ1 choice, the masters/exports split — see the
companion design doc [`storage-server.md`](storage-server.md). This runbook is
the *how*; that doc is the *why*. Where they overlap (slot numbers, pool names),
`storage-server.md` is the source of truth.

> **Product-name note:** TrueNAS dropped the old "CORE vs. SCALE" branding. The
> free, open-source download is now labeled **"TrueNAS Community Edition"** on
> the website. It is the direct continuation of what used to be called *TrueNAS
> SCALE* (the Linux-based line); the ISO filename still contains `SCALE`. The
> older FreeBSD-based *TrueNAS CORE* is discontinued — do **not** download it.
> This runbook targets **TrueNAS Community Edition 25.10 ("Goldeye")**.

---

## What you are building (end state)

| Pool | Drives (bay slots) | ZFS layout | ~Usable | Holds |
|------|--------------------|------------|---------|-------|
| **boot** | 2× M.2 | mirror | — | the OS only |
| **masters** | 14×400 GB + 2×480 GB (slots 5, 6, 11–24) | one 16-wide **RAIDZ2** vdev | ~5.2 TiB | kiln `$MASTERS_ARCHIVE` — ProRes masters |
| **exports** | 4×900 GB + 4×800 GB (slots 1–4, 7–10) | two vdevs in one pool: **RAIDZ1**(4×900) + **RAIDZ1**(4×800) | ~4.7 TiB | kiln `$EXPORTS_ARCHIVE` — export packages + general shares |

Two datasets get shared to the rest of the network:

- `masters/kiln` — exported over **NFS**, mounted on deb005 as `masters_archive`.
- `exports/kiln` — exported over **NFS** (for deb005) **and SMB** (for the Mac,
  and any general file sharing), mounted on deb005 as `exports_archive`.

deb005 then points its `config.toml` at those two mounts and kiln is done.

---

## Before you start — checklist

- [ ] The **UCSC-SAS-M5HD** HBA is installed and CIMC shows all 24 SAS drives as
      **JBOD / Good** (already verified 2026-07-01 — see `storage-server.md`).
- [ ] The **2× M.2** boot devices are present (on their own mini-controller, not
      the M5HD — they will **not** appear in the M5HD's JBOD list; that is normal).
- [ ] A **USB stick** (8 GB+) you can erase, to write the installer to.
- [ ] The server is on the **10 GbE** network with the Mac and deb005.
- [ ] You can reach the server console — either a monitor + keyboard on the
      C240 M5, or the **CIMC** remote KVM (Cisco's out-of-band console; open the
      CIMC IP in a browser → **Launch KVM**). CIMC KVM is easier — no crash cart.

> **Cost:** TrueNAS Community Edition is free and open-source. Downloading and
> running it is **$0**. The only paid TrueNAS offering is enterprise
> support/appliances, which this build does not use. No approval needed.

---

## Step 1 — Download the installer ISO

1. Go to **<https://www.truenas.com/download/>**.
2. Download the **TrueNAS Community Edition** stable release — the file named
   like **`TrueNAS-SCALE-25.10.4.iso`** (~2–3 GB). Take the **stable** build.
   - **Do NOT** download `TrueNAS-26.0.0-BETA.x.iso` — a beta is not appropriate
     for an irreplaceable-masters archive.
   - Ignore the `.update` file — that is for upgrading an existing install, not a
     fresh one.

## Step 2 — Write the ISO to the USB stick

Pick your workstation OS. In every case, replace the device name with **your**
USB stick and write to the **whole disk**, not a partition.

**Debian / Ubuntu**

```bash
lsblk                      # identify the USB disk, e.g. /dev/sdb (NOT /dev/sdb1)
sudo dd if=TrueNAS-SCALE-25.10.4.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

(Or use the GUI **balenaEtcher**, or Ubuntu's **Startup Disk Creator**.)

**RHEL / Fedora**

```bash
sudo dnf install mediawriter        # once, if you want the GUI
# GUI: Fedora Media Writer → Select .iso → Select USB → Write
# or the same dd command as above
sudo dd if=TrueNAS-SCALE-25.10.4.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

**Windows**

- Use **Rufus** (<https://rufus.ie>) or **balenaEtcher**. In Rufus: select the
  ISO, select the USB device, keep the default write mode (accept "DD Image mode"
  if prompted), click **Start**.

**macOS**

- Easiest: **balenaEtcher** (<https://etcher.balena.io>) — select ISO, select
  USB, Flash.
- Or terminal:
  ```bash
  diskutil list                       # find the USB, e.g. /dev/disk4
  diskutil unmountDisk /dev/diskN
  sudo dd if=TrueNAS-SCALE-25.10.4.iso of=/dev/rdiskN bs=4m
  diskutil eject /dev/diskN
  ```

## Step 3 — Boot the server from the USB stick

1. Insert the USB stick (or, over CIMC KVM, map the ISO directly as virtual media
   — **Virtual Media → Map** the `.iso`, no physical stick needed).
2. Power on / reboot. Press the Cisco boot-menu hotkey (**F6** for the one-time
   boot menu on a C240 M5; **F2** enters BIOS setup if you need it).
3. Choose to boot the **USB stick** (or the mapped virtual CD/DVD) in **UEFI
   mode**. The TrueNAS installer loads.

## Step 4 — Install TrueNAS onto the M.2 boot mirror

The installer is a simple blue text-menu. Screen by screen:

1. **Console setup menu →** select **`Install/Upgrade`**.
2. **Choose destination media —** the installer lists available drives.
   **Select BOTH M.2 devices** here (use the spacebar to check each, so TrueNAS
   creates a **boot-pool mirror** across the two).
   - **Only the M.2 devices.** Do **not** select any of the 24 SAS SSDs — those
     become your data pools later and must stay untouched.
   - **If the M.2 module presents as a single hardware-RAID volume** (one device)
     instead of two, that mini-controller is already mirroring them — just select
     that one device and continue. (You confirmed 2× M.2 boot is present; check
     here whether it shows as one or two.)
3. A warning appears: **"This erases the contents of the selected drive!"** —
   select **`Yes`**. (This is only erasing the M.2 boot media, not your data
   drives.)
4. **Account creation →** select **`1 Administrative user (truenas_admin)`**, then
   **`OK`**.
5. **Set the password** for the `truenas_admin` account — type a strong password
   twice. **Record it in your password manager now.**
6. **Legacy Boot prompt →** select **`Yes`** for UEFI boot (the C240 M5 booted the
   installer in UEFI mode, so keep UEFI).
7. Wait for **`Installation Succeeded`** → **`OK`**.
8. Back at the menu, choose to **reboot**, and **remove the USB stick** (or
   un-map the virtual media in CIMC) so it boots from the M.2 this time.

## Step 5 — Reach the web UI

1. After reboot, the server drops to a **console setup menu**. At the **top of
   that menu** it prints the **IP address** it got via DHCP (e.g.
   `https://172.31.x.x`).
2. On your workstation, open that address in a browser.
3. Log in as **`truenas_admin`** with the password from Step 4.

> **Recommended (optional): give it a static IP.** A storage server should not
> move addresses. In the UI: **Network → Interfaces → (click the interface) →**
> turn **off** DHCP, add a static **IP Address** (with the right CIDR, e.g.
> `/24`), set the gateway/DNS under **Network → Global Configuration**, then
> **Test Changes** and **Save Changes**. If you skip this, at least reserve the
> DHCP lease on your router so the address is stable — deb005's mounts depend on
> it not changing.

## Step 6 — Create the `masters` pool (16-wide RAIDZ2)

1. Left nav → **Storage**. This is the **Storage Dashboard**.
2. Top-right → **`Create Pool`**. The **Pool Creation Wizard** opens (a numbered
   sequence of screens).
3. **Screen 1 — General Info / Name:**
   - **Name:** type **`masters`** (lowercase).
   - Leave **encryption off** (an on-prem archive with local keys — encryption
     adds key-management risk with little benefit here; skip it).
   - Click **Next**.
4. **Screen 2 — Data (the data vdev):**
   - **Layout** dropdown → choose **`RAIDZ2`**.
   - You want **one 16-disk vdev** of the 400/480 GB drives. The cleanest way to
     pick exact disks is **`Manual Disk Selection`** (in the Advanced area):
     - Click **`Manual Disk Selection`**.
     - The 24 SAS drives are grouped by size. Select the **fourteen ~400 GB**
       drives (raw ≈ 381554 MB, slots 11–24) **and** the **two ~480 GB** drives
       (raw ≈ 457862 MB, slots 5–6) — **16 disks total** — into a **single vdev**.
     - Confirm the vdev shows **RAIDZ2** with **16** disks.
     - > The two 480 GB drives are padded down to ~400 GB inside the vdev — this
       > ~80 GB/drive waste is expected and accepted (see the design doc).
   - *(If you use Automated Disk Selection instead:* set disk size to the 400 GB
     group with **"Treat Disk Size as Minimum"** on so the 480s qualify, **Width
     = 16**, **Number of VDEVs = 1**.)*
   - Do **not** configure Log / Cache / Spare / Metadata / Dedup — click
     **`Save And Go To Review`** to skip the optional screens.
5. **Review screen:** confirm it reads **one RAIDZ2 vdev, 16 wide**, pool name
   **`masters`**. Click **`Create Pool`**.
6. Wait for it to finish. `masters` now appears on the Storage Dashboard.

## Step 7 — Create the `exports` pool (two RAIDZ1 vdevs in one pool)

1. **Storage** → **`Create Pool`** again.
2. **Screen 1 — Name:** **`exports`**, encryption off, **Next**.
3. **Screen 2 — Data:** you need **two** data vdevs in this one pool.
   - **Layout** → **`RAIDZ1`**.
   - Click **`Manual Disk Selection`**.
   - **First vdev:** select the **four ~900 GB** drives (raw ≈ 915715 MB, slots
     1–4) as a **RAIDZ1** vdev.
   - Add a **second vdev**: click **`Add`** (adds another VDEV area), set it to
     **RAIDZ1**, and select the **four ~800 GB** drives (raw ≈ 763097 MB, slots
     7–10).
   - Confirm **two RAIDZ1 vdevs** (4 disks each) are staged.
   - > Never mix the 900 GB and 800 GB drives *inside one vdev* — ZFS would pad
     > every disk down to the smallest. Keeping them in separate same-size vdevs
     > is why there are two vdevs here.
   - **`Save And Go To Review`**.
4. **Review:** confirm **two RAIDZ1 vdevs** under pool **`exports`** → **`Create
   Pool`**.
5. `exports` now appears on the dashboard alongside `masters`.

> **Do NOT add SLOG (Log) or L2ARC (Cache) vdevs.** This is a write-once
> sequential archive over 10 GbE — those caches add cost and complexity with no
> benefit here (see the design doc's rejected-alternatives section).

## Step 8 — Create the datasets

Datasets are the actual folders you share — never share a whole pool.

1. Left nav → **Datasets**.
2. Select the **`masters`** pool row, then **`Add Dataset`**.
   - **Name:** **`kiln`** → this creates `masters/kiln`.
   - **Dataset Preset / Share Type:** choose **`Generic`** (this is an NFS-only
     share for a Linux client; the SMB-optimized preset is only needed for the
     SMB share). Accept the defaults otherwise → **Save**.
3. Select the **`exports`** pool row, then **`Add Dataset`**.
   - **Name:** **`kiln`** → this creates `exports/kiln`.
   - Because this dataset is shared over **both** SMB (Mac) and NFS (deb005),
     choose the **`SMB`** preset (it sets SMB-friendly case-insensitivity and
     ACLs; NFS still works on top of it) → **Save**.

You now have `/mnt/masters/kiln` and `/mnt/exports/kiln` on the server (TrueNAS
mounts pools under `/mnt/<pool>`).

## Step 9 — Share the datasets over NFS (for deb005)

deb005 is Linux and mounts both archives over NFS.

1. Left nav → **Shares**.
2. Under **Unix (NFS) Shares**, click **`Add`**. The **Add NFS Share** screen opens.
   - **Path:** browse to **`/mnt/masters/kiln`**.
   - **Description:** `kiln masters archive`.
   - Expand **Advanced Options** to lock it down:
     - **Networks / Hosts:** restrict to deb005 only — enter deb005's IP (or the
       LAN subnet, e.g. `172.31.x.0/24`). Do not leave it open to everyone.
     - **Mapall User / Mapall Group:** set both to the account that should own the
       files (for a single-user, no-accounts setup, mapping to `root`/`wheel` or a
       dedicated `kiln` user is simplest so deb005 can write freely). Pick one and
       be consistent across both shares.
   - **Save**.
3. Click **`Add`** again and repeat for **`/mnt/exports/kiln`** (description
   `kiln exports archive`, same Networks/Hosts and Mapall settings).
4. If prompted to **enable the NFS service**, say **yes** (or **System Settings →
   Services → NFS →** toggle **Running** on, and set **Start Automatically**).

## Step 10 — Share `exports/kiln` over SMB (for the Mac)

The Mac uses SMB (macOS's native network-share protocol) for general file access.
Only `exports/kiln` needs SMB; masters are deb005-only.

1. **Shares →** under **Windows (SMB) Shares**, click **`Add`**.
   - **Path:** **`/mnt/exports/kiln`**.
   - **Name:** **`kiln-exports`** (this is the share name the Mac sees).
   - **Save**.
2. If prompted to **enable the SMB service**, say **yes** (or **System Settings →
   Services → SMB →** **Running** on, **Start Automatically**).
3. Create a user for the Mac to authenticate as (SMB needs an account):
   **Credentials → Local Users → `Add`** — make a user (e.g. `mac`), give it a
   password, and ensure the `exports/kiln` dataset permissions allow it access
   (**Datasets → exports/kiln → Edit Permissions**).
4. **On the Mac:** Finder → **Go → Connect to Server** (⌘K) →
   `smb://<server-ip>/kiln-exports` → authenticate with that user.

> Masters are intentionally **not** shared over SMB — the only writer to the
> masters pool is deb005 (kiln), over NFS. Fewer doors into the irreplaceable
> data.

## Step 11 — Mount both shares on deb005 and point kiln at them

On **deb005** (Debian):

1. Install the NFS client (once):
   ```bash
   sudo apt update && sudo apt install -y nfs-common
   ```
2. Create local mount points:
   ```bash
   sudo mkdir -p /mnt/masters /mnt/exports
   ```
3. Test-mount both (replace `<server-ip>` with the storage server's static IP):
   ```bash
   sudo mount -t nfs <server-ip>:/mnt/masters/kiln /mnt/masters
   sudo mount -t nfs <server-ip>:/mnt/exports/kiln /mnt/exports
   df -h /mnt/masters /mnt/exports          # confirm both show the server + size
   touch /mnt/masters/.kiln-write-test && rm /mnt/masters/.kiln-write-test  # write check
   touch /mnt/exports/.kiln-write-test && rm /mnt/exports/.kiln-write-test
   ```
   If the write test fails with a permissions error, revisit the **Mapall**
   settings in Step 9.
4. Make the mounts persist across reboot — add to **`/etc/fstab`**:
   ```
   <server-ip>:/mnt/masters/kiln  /mnt/masters  nfs  defaults,_netdev,nofail  0  0
   <server-ip>:/mnt/exports/kiln  /mnt/exports  nfs  defaults,_netdev,nofail  0  0
   ```
   - `_netdev` waits for the network; `nofail` lets deb005 still boot if the
     storage server is down (kiln then holds jobs in its pending-archive queue,
     by design). Apply with `sudo mount -a`.
5. Point kiln at the two mounts — in deb005's **`config.toml`**:
   ```toml
   masters_archive = "/mnt/masters"
   exports_archive = "/mnt/exports"
   ```
6. Verify end to end:
   ```bash
   kiln doctor
   ```
   It should report both archive paths **reachable and writable**. Any job
   already sitting in the local pending-archive queue will drain to the server on
   the next pass.

---

## Verification (the whole thing works)

- **Storage → Storage Dashboard:** `masters` shows one RAIDZ2 vdev (16 wide);
  `exports` shows two RAIDZ1 vdevs; both **Healthy**.
- **`kiln doctor` on deb005:** both `masters_archive` and `exports_archive`
  reachable/writable.
- **Round-trip:** drop a small test master into kiln's `$INBOX`; after processing,
  confirm the master lands in `/mnt/masters/<job_id>/` and the export package in
  `/mnt/exports/<job_id>/`.
- **Mac SMB:** `smb://<server-ip>/kiln-exports` mounts and lists files.

## Post-build housekeeping

- **Enable scrubs:** **Data Protection → Scrub Tasks** — a periodic scrub is
  usually created per pool by default; confirm both `masters` and `exports` have
  one (monthly is fine for SSD). Scrubs are how ZFS catches and self-heals bit
  rot on the irreplaceable masters.
- **Enable SMART tests:** **Data Protection → S.M.A.R.T. Tests** — add a short
  test (e.g. weekly) across all data disks so a failing drive is flagged early.
- **Alerts:** **System Settings → Alert Settings** — set an email (or other)
  destination so a degraded pool or failed drive actually reaches you.
- **Snapshots (optional):** the masters are write-once so snapshots add little,
  but a periodic snapshot task on `exports/kiln` cheaply guards against an
  accidental delete of a finished package.

---

## Firmware note

If CIMC/BIOS flags an HBA firmware compatibility warning, update the M5HD
firmware via the **Cisco HUU** (Host Upgrade Utility) before relying on the pools.
The C240 M5 storage controller sits in a dedicated internal socket, not a normal
PCIe slot.

---

*Sources for the TrueNAS UI steps (25.10 "Goldeye"): the official TrueNAS
Documentation Hub — Installing TrueNAS, Pool Creation Wizard, Adding NFS Shares,
and Windows (SMB) Shares pages under `truenas.com/docs/scale/25.10/`.*
