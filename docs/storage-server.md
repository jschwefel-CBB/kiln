# Storage Server — Design & Build Notes

The storage server is the archive destination for kiln. kiln processes video on the
GPU box (`deb005`) using its local NVMe, then moves the finished work here as the
final step — **split across two pools**: the ProRes master goes to the `masters` pool
(`$MASTERS_ARCHIVE`), and the finished export package (upload video +
captions/transcript/chapters/metadata) goes to the `exports` pool
(`$EXPORTS_ARCHIVE`). This box is **archive + file shares only** — no scratch (that
lives on deb005), no heavy compute.

Both pools are **RAIDZ2 (double-parity)**. The role split drives *which drives* go
where, not the parity level (see "ZFS layout" for the durability reasoning):
`masters` holds transient data (pruned after the retention window) on the older,
smaller SSDs; `exports` holds keep-forever, effectively-irreplaceable data on the
newer, larger drives.

Status as of 2026-07-02: hardware assembled and verified in CIMC; **pools not yet
created / TrueNAS not yet installed.** (An initial `masters` pool was configured in
the TrueNAS UI on 2026-07-02 and then exported/destroyed to re-lay-out the disks for
the drive plan below.)

## Hardware

- **Chassis:** Cisco UCS C240 M5 SFF, 26 drive bays (24 front + 2 rear).
- **Storage controller:** Cisco **UCSC-SAS-M5HD** — an IT-mode-only HBA (Cisco-stamped
  LSI 9300-8i family). Presents every drive raw/JBOD to the OS, which is what ZFS needs.
  Already installed (user swapped one in from another UCS server — zero cost). Labeled
  "MRAID" in CIMC but it is the SAS (HBA) variant, confirmed showing all drives as JBOD.
- **Boot:** 2× M.2 SSD on a **separate** mini-controller (not the M5HD). Confirmed present
  in CIMC. TrueNAS installs here as a boot mirror.
- **Network:** 10 GbE (same as Mac + deb005). A 4K ProRes master (~220 GB/hr) copies in
  ~1–2 min at line rate — network is not a bottleneck.
- **RAM:** ECC (C240 M5). ZFS prefers ECC — confirm DIMMs populated.

### Drive inventory

All SAS-bay drives report **JBOD / Good** via the M5HD (raw for ZFS). Note: originally
20×400 GB, but 4 were SATA — those were pulled and replaced with 4×800 GB SAS.

**As verified in CIMC 2026-07-01 (the drives physically in the box then):**

| Slots | Count | ~Size | Raw (MB) | Models |
|-------|-------|-------|----------|--------|
| 1–4   | 4     | ~900 GB | 915715 | ATA SSD (fw GXT50F3Q) |
| 5–6   | 2     | ~480 GB | 457862 | TOSHIBA SSD (010B) |
| 7–10  | 4     | ~800 GB | 763097 | MICRON SSD (MB19) |
| 11–24 | 14    | ~400 GB | 381554 | **mixed** TOSHIBA (0107) + HGST (D170) |

**Drive plan change (2026-07-02):** a cache of **10× 1.8 TB** SAS SSDs is being brought
in, and the box is filled to all **26 bays**. Final populated layout:

- **Slots 1–10:** the **10× 1.8 TB** drives → `exports` pool.
- **Slots 11–26:** **16 drives** — the **14× 400 GB** (mixed TOSHIBA/HGST) + the
  **2× 480 GB** (padded to ~400 GB in the vdev) → `masters` pool.

The **4×900 GB and 4×800 GB** drives come **out** (no bay left once 10 + 16 fill all 26)
and go on the shelf as spares. That includes the one 800 GB drive that dropped and
returned after a reseat — a separate issue (verify its SMART before ever trusting it),
but moot here since all the 800s are out of the box regardless.

Bay math: 10 (exports) + 16 (masters) = **26 = every bay full, nothing empty.**
Slots 25–26 are the **2 rear bays** — confirm they show as JBOD/Good on the M5HD once
populated (the rear pair is the part that occasionally hangs off a different connector).

- The M.2 boot pair does **not** appear in the M5HD JBOD list (it's on its own controller) — this is expected.
- Mixed vendors in the 400 GB group is fine for ZFS (it only cares about size, not vendor/firmware).

## ZFS layout (TrueNAS SCALE)

Chosen to **maximize usable capacity while keeping the irreplaceable masters safe**.
Software RAID via ZFS was chosen over the hardware RAID controller for end-to-end
checksums + self-healing + snapshots + pool portability (no controller lock-in) — better
for an irreplaceable archive and for acquisition optics.

| Pool | Drives (slots) | ZFS layout | ~Usable | Role |
|------|----------------|------------|---------|------|
| **boot** | 2× M.2 | mirror | — | OS only |
| **exports** | 10×1.8 TB (slots 1–10) | **one 10-wide RAIDZ2 vdev** | **~13 TiB** (forever) | kiln `$EXPORTS_ARCHIVE` — finished export packages, kept forever + general SMB shares |
| **masters** | 14×400 + 2×480 (slots 11–26) | **one 16-wide RAIDZ2 vdev** | **~5.2 TiB** (~26 hr 4K) | kiln `$MASTERS_ARCHIVE` — ProRes masters; **transient**, pruned after the retention window |
| | | | **~18 TiB total** | |

- The two ~480 GB drives are padded down to ~400 GB inside the `masters` RAIDZ2 vdev —
  ~80 GB/drive of waste, accepted for simplicity (they'd otherwise sit idle).

**Why the big drives hold `exports` and the small drives hold `masters` (durability-first, not capacity-first):**

The obvious instinct is "big drives → big data → masters." That's wrong here, because
**master pruning caps how much master ever accumulates.** At ~220 GB/hr for 4K ProRes,
the ~5.2 TiB `masters` pool holds ~26 hours of 4K master — more than a 90-day *active-edit*
window realistically needs for one creator. Masters are never capacity-starved, because
they're deleted before they can pile up.

Meanwhile the export package is small but **kept forever — and becomes effectively
irreplaceable once its master is pruned** (after pruning there is no master to re-run
kiln against). So the assignment is driven by *durability of the irreplaceable pool*:

- **`exports` → the 10× 1.8 TB drives** (newer, fewer, larger, healthier). The
  keep-forever, irreplaceable-after-prune data rides the best hardware, in a clean
  10-wide RAIDZ2. ~13 TiB is far more than needed — the forever pool *should* have the
  headroom.
- **`masters` → the 16 smaller drives** (14× 400 GB mixed-vendor + 2× 480 GB padded to
  400; older, worn). Transient, pruned-at-90-days data is exactly what belongs on the
  more-disposable drives: a lost master inside its edit window can, worst case, be
  re-exported from the Mac's FCP project; a lost master past 90 days was going to be
  deleted anyway.

Both pools are **RAIDZ2** — this reverses the earlier design's cheap-single-parity
choice for exports in the healthiest direction. The earlier RAIDZ1-for-exports plan
leaned on "exports are re-derivable," which only holds for the 90 days the master
survives; RAIDZ2 on the big drives closes that gap permanently.

**Capacity caveat:** ~26 hr of 4K master is the tight number (the two 480 GB drives are
already in this pool). If more than ~26 hr of un-pruned 4K masters could ever be in
flight at once, the lever is retention (shorten the window).

- Within each pool, subdivide with **datasets** (e.g. `masters/kiln`, `exports/kiln`,
  `exports/photos`) for independent shares/snapshots/quotas — datasets give the "one
  namespace" feel without merging failure domains.

### Capacity reality (why retention matters)

At ~220 GB/hr for 4K ProRes 422 HQ, the ~5.2 TiB `masters` pool holds only **~26 hours** of
4K masters (or ~100 hrs of 1080p ProRes HQ). This is why kiln has a **master-retention/pruning**
policy (keep the master for a rolling window — default 90 days — then delete it from the
`masters` pool). This small, transient pool is deliberately on the older 400 GB drives.
The compressed upload + artifacts are kept permanently on the separate `exports` pool
(the 1.8 TB drives, ~13 TiB), and a master is pruned only after kiln confirms those
exports are present there. See the kiln design spec.

## Design decisions made & rejected

- **Software RAID (ZFS) over hardware RAID** — chosen for checksums/self-heal/snapshots/
  portability. Required an IT-mode HBA (the M5HD), which the box now has.
- **One giant pool across all 24 drives — REJECTED.** Adds ~0 usable capacity vs separate
  pools, and a single vdev failure would destroy ALL data including masters. Usable capacity
  comes from the parity choice (RAIDZ1 vs Z2), not from merging pools.
- **Mixed-size drives in one RAIDZ vdev — REJECTED.** ZFS pads every disk in a vdev down to
  the smallest, which would waste the 800/900 GB capacity. Hence separate pools by size.
- **16-wide RAIDZ2 (not 2×8-wide) for the `masters` pool** — one wide double-parity vdev gives
  more usable capacity (only 2 drives to parity) while keeping double-parity safety. Acceptable
  rebuild time on SSD.
- **10-wide RAIDZ2 for the `exports` pool** — one clean double-parity vdev over the 10× 1.8 TB
  drives (~13 TiB). RAIDZ1 was **rejected** here: exports are "re-derivable" only for the 90 days
  the master survives pruning, after which the package is effectively irreplaceable — so the
  forever pool gets double parity on the newer/larger drives.
- **Big drives → exports, small drives → masters (durability-first) — DECIDED 2026-07-02.** Not
  "big data → big drives": pruning caps master accumulation (~26 hr of 4K fits the ~5.2 TiB pool),
  so masters are never capacity-starved and can sit on the older, worn 400 GB SSDs. The
  irreplaceable-after-prune exports ride the newer, fewer 1.8 TB drives. See the ZFS-layout
  rationale above.
- **900 GB + 800 GB drives in `exports` — SUPERSEDED 2026-07-02.** The original 4×900 + 4×800
  two-RAIDZ1 `exports` pool is replaced by 10× 1.8 TB RAIDZ2. The 900s/800s are pulled and shelved.
- **Automatic tiering (hot SSD → cold, LRU spill-down) — REJECTED / not available.** ZFS and
  TrueNAS SCALE do not support disk-speed autotiering (iX explored autotier/gluster, abandoned).
  L2ARC and special-vdev are caches, not tiering. Also pointless here: a write-once archive of
  huge sequential files on all-same-speed SAS SSD has no hot working set to accelerate.
- **iSCSI block mounts (instead of NFS/SMB) — REJECTED.** iSCSI exports a raw block LUN that the
  client formats with its own filesystem, which is the wrong model for this archive:
  - It makes the data **opaque to ZFS** — the server sees one blob, not files, so you lose
    per-file snapshots/visibility and ZFS can't tell you *which master* took a bad block. The
    whole reason for choosing ZFS was file-level integrity on the irreplaceable masters.
  - It is effectively **single-writer** — a LUN is owned by one host's filesystem. This design
    has two clients (deb005 writes over NFS, the Mac browses `exports` over SMB); NFS/SMB handle
    multiple clients on one dataset natively, iSCSI does not.
  - It **defeats the pending-archive resilience** — kiln assumes a file share that is simply
    reachable or not; a block device that disappears mid-write can corrupt the client-side
    filesystem, a far worse failure than a stale NFS mount (handled cleanly by `nofail`).
  - **macOS has no native iSCSI initiator** (needs a ~$195 third-party kext); SMB is built into
    Finder.
  - **No speed benefit** for this workload — iSCSI wins on low-latency random small-block I/O
    (databases, VM disks); kiln does huge sequential ProRes copies that already saturate 10 GbE
    over NFS. (iSCSI *would* be a reasonable backing store for a future VM/container workload on
    the `exports` pool's general-purpose space — but that is a separate use case, not the kiln
    archive path.)

## Build steps (to do — not yet executed)

0. **Re-lay-out disks (2026-07-02) — fill all 26 bays:** pull the 4×900 and 4×800 (shelf as
   spares). Install the **10× 1.8 TB drives in slots 1–10** and arrange the **16 masters drives
   (14× 400 + 2× 480) in slots 11–26**. If any drive was previously in a ZFS pool, **wipe it**
   first (Storage → Disks → select → Wipe → Quick) so stale labels don't block the rebuild.
1. **Verify:** M5HD shows all 26 drives as JBOD/Good — including the 2 rear bays (slots 25–26),
   which the masters pool now uses. M.2 boot present.
2. **Install TrueNAS** onto the 2× M.2 as a boot mirror (select both M.2 in the installer;
   confirm whether the M.2 module presents one hardware-RAID volume or two devices to mirror).
3. **Create `exports` pool:** the 10× 1.8 TB drives (slots 1–10), layout RAIDZ2, one vdev of 10.
4. **Create `masters` pool:** the 16 drives in slots 11–26 (14× 400 + 2× 480), layout RAIDZ2,
   one vdev of 16 (the 480s pad to ~400 inside the vdev).
5. **Datasets + sharing:** create `masters/kiln` and `exports/kiln`; enable **NFS** (for deb005)
   on both, and **SMB** (for the Mac) on `exports/kiln` (and any other general share).
6. **Mount both on deb005** (NFS) and set kiln `config.toml`:
   `masters_archive = "<masters/kiln mountpoint>"` and `exports_archive = "<exports/kiln mountpoint>"`.
7. Do **not** add SLOG/L2ARC — unnecessary for write-once sequential archive over 10 GbE.

A full zero-prior-experience build runbook (with the TrueNAS GUI click paths for
installing, creating both pools, sharing, and mounting on deb005) lives in
[`storage-server-runbook.md`](storage-server-runbook.md).
