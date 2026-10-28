// SPDX-License-Identifier: MPL-2.0
//! Guest side of the bootable A/B updater (docs/AB_UPDATER.md).
//!
//! The transaction policy mirrors tools/ab_update.py and shares its
//! control-record schema (synveil.ab-control/v2), but this module is
//! the installed-system implementation: it operates on the real GPT
//! slot partitions, and its results are never interchangeable with
//! the host fixture.
//!
//! Boot selection lives entirely inside Synveil's own boot path:
//! this module NEVER writes UEFI variables (no BootOrder, BootNext
//! or Boot#### writes — firmware-variable changes require the
//! owner's explicit authorization, which the updater does not
//! hold). Whichever generation the firmware loads, its init hook
//! converges to the desired slot (trial first, else confirmed) by
//! chain-loading that slot's image with kexec_file_load, after the
//! attempt accounting is durable. Firmware boot order and other
//! systems' entries are untouched by construction.
//!
//! Trust: the publisher key is pinned at build time in
//! /etc/synveil/publisher.json (provisioned with the generation,
//! never from a release). The current key is the development key
//! synveil-dev-2026-01; every record carries that fact.
//!
//! Safety invariants held here:
//! * the running/confirmed slot partition is never opened for writing;
//! * the trial record is persisted (atomic rename + fsync) BEFORE
//!   any chain-load transfer, and a slot is chain-loaded only after
//!   its bytes are re-read and re-hashed from the slot itself;
//! * attempt accounting happens in the init hook, before services;
//! * any uncertainty about layout, records or digests fails closed.
//!
//! Confirmation is two-key (owner Priority 2 amendment,
//! docs/AB_UPDATER.md): the agent's health checks must pass on the
//! booted trial AND the owner must issue the typed `confirm` action
//! (the desktop update GUI, or `veil-ab request confirm`). Passing
//! health alone never promotes a generation; a healthy trial the
//! owner neither confirms nor rolls back consumes one attempt per
//! boot and falls back when its attempts are spent.

use crate::ed25519;
use crate::sha256::{Sha256, hex};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const CONTROL_SCHEMA: &str = "synveil.ab-control/v2";
pub const RELEASE_SCHEMA: &str = "synveil.ab-release/v2";
pub const TRUST_SCHEMA: &str = "synveil.ab-trust/v1";
pub const STAMP_SCHEMA: &str = "synveil.generation/v1";
pub const SLOT_IDENTITY_SCHEMA: &str = "synveil.slot-generation/v1";

pub const ESP_PARTUUID: &str = "ab000000-0000-4000-8000-0000000000e5";
pub const SYSTEM_A_PARTUUID: &str = "ab000000-0000-4000-8000-00000000000a";
pub const SYSTEM_B_PARTUUID: &str = "ab000000-0000-4000-8000-00000000000b";
pub const DATA_PARTUUID: &str = "ab000000-0000-4000-8000-0000000000da";
pub const RECOVERY_PARTUUID: &str = "ab000000-0000-4000-8000-0000000000ec";
pub const RELEASE_PARTUUID: &str = "ab000000-0000-4000-8000-0000000000f1";
pub const STATE_PARTUUID: &str = crate::credential::STATE_PARTUUID;

pub const ENABLED_MARKER: &str = "/etc/synveil/ab-system";
pub const TRUST_PATH: &str = "/etc/synveil/publisher.json";
pub const STAMP_PATH: &str = "/etc/synveil/generation.json";
pub const CONTROL_PATH: &str = "/var/lib/synveil/ab-control.json";
pub const RUNTIME_DIR: &str = "/run/synveil-ab";
pub const SOCKET_PATH: &str = "/run/synveil-ab/agent.sock";
pub const GEN_EFI_PATH: &str = "EFI/synveil/gen.efi";
pub const GEN_JSON_PATH: &str = "EFI/synveil/gen.json";

pub const TRIAL_ATTEMPTS: u32 = 3;
const MAX_HISTORY: usize = 32;
const MAX_RELEASE_BYTES: u64 = 512 * 1024 * 1024;

fn err(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Print a proof marker to the console. The daemon and the init hook
/// both run with the console as stdout in the guest.
pub fn marker(text: &str) {
    println!("{text}");
    let _ = io::stdout().flush();
}

// ---------------------------------------------------------------------
// Disk layout

#[derive(Clone, Debug)]
pub struct Partition {
    pub devname: String,
    pub number: u32,
    pub start_lba: u64,
    pub size_lba: u64,
    pub partuuid: String,
    pub partname: String,
}

impl Partition {
    pub fn bytes(&self) -> u64 {
        self.size_lba * 512
    }
    pub fn device(&self) -> String {
        format!("/dev/{}", self.devname)
    }
}

pub fn scan_partitions() -> io::Result<Vec<Partition>> {
    let mut out = Vec::new();
    for entry in fs::read_dir("/sys/class/block")? {
        let entry = entry?;
        let dir = entry.path();
        let Ok(uevent) = fs::read_to_string(dir.join("uevent")) else {
            continue;
        };
        if !uevent.lines().any(|l| l == "DEVTYPE=partition") {
            continue;
        }
        let field = |key: &str| -> Option<String> {
            uevent
                .lines()
                .find_map(|line| line.strip_prefix(&format!("{key}=")).map(str::to_string))
        };
        let (Some(partn), Some(partuuid)) = (field("PARTN"), field("PARTUUID")) else {
            continue;
        };
        let read_u64 = |name: &str| -> io::Result<u64> {
            fs::read_to_string(dir.join(name))?
                .trim()
                .parse()
                .map_err(|_| err("invalid sysfs block value"))
        };
        out.push(Partition {
            devname: entry.file_name().to_string_lossy().into_owned(),
            number: partn.parse().map_err(|_| err("invalid PARTN"))?,
            start_lba: read_u64("start")?,
            size_lba: read_u64("size")?,
            partuuid: partuuid.to_lowercase(),
            partname: field("PARTNAME").unwrap_or_default(),
        });
    }
    Ok(out)
}

pub fn find_partition(partitions: &[Partition], partuuid: &str) -> io::Result<Option<Partition>> {
    let mut found: Option<Partition> = None;
    for partition in partitions {
        if partition.partuuid == partuuid {
            if found.is_some() {
                return Err(err("duplicate partition identity; failing closed"));
            }
            found = Some(partition.clone());
        }
    }
    Ok(found)
}

pub struct Layout {
    pub esp: Partition,
    pub slot_a: Partition,
    pub slot_b: Partition,
}

impl Layout {
    pub fn load() -> io::Result<Layout> {
        let partitions = scan_partitions()?;
        let need = |uuid: &str, what: &str| -> io::Result<Partition> {
            find_partition(&partitions, uuid)?
                .ok_or_else(|| err(&format!("{what} partition missing")))
        };
        Ok(Layout {
            esp: need(ESP_PARTUUID, "ESP")?,
            slot_a: need(SYSTEM_A_PARTUUID, "system A")?,
            slot_b: need(SYSTEM_B_PARTUUID, "system B")?,
        })
    }

    pub fn slot(&self, slot: &str) -> &Partition {
        if slot == "A" {
            &self.slot_a
        } else {
            &self.slot_b
        }
    }
}

// ---------------------------------------------------------------------
// Mounting (busybox mount, as init uses; unmount on drop)

pub struct Mount {
    dir: PathBuf,
}

impl Mount {
    pub fn attach(
        partition: &Partition,
        fstype: &str,
        name: &str,
        read_only: bool,
    ) -> io::Result<Mount> {
        let dir = Path::new(RUNTIME_DIR).join("mnt").join(name);
        fs::create_dir_all(&dir)?;
        let mut args = vec!["-t", fstype];
        if read_only {
            args.extend(["-o", "ro"]);
        }
        let device = partition.device();
        let dir_text = dir.to_string_lossy().into_owned();
        args.push(&device);
        args.push(&dir_text);
        let status = Command::new("/bin/mount").args(&args).status()?;
        if !status.success() {
            return Err(err(&format!("mount of {name} failed")));
        }
        Ok(Mount { dir })
    }

    pub fn path(&self) -> &Path {
        &self.dir
    }
}

impl Drop for Mount {
    fn drop(&mut self) {
        let _ = Command::new("/bin/umount").arg(&self.dir).status();
    }
}

fn sync_dir(path: &Path) -> io::Result<()> {
    fs::File::open(path)?.sync_all()
}

// ---------------------------------------------------------------------
// Records

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Generation {
    pub slot: String,
    pub version: u64,
    pub bytes: u64,
    pub sha256: String,
    pub state_format: u64,
    pub generation_id: String,
    pub min_reader_state_format: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Trial {
    pub slot: String,
    pub version: u64,
    pub bytes: u64,
    pub sha256: String,
    pub state_format: u64,
    pub generation_id: String,
    pub min_reader_state_format: u64,
    pub attempts: u32,
    pub awaiting_health: bool,
    /// Set by the init hook when it has already accounted this
    /// trial boot and is chain-loading the trial image, so the
    /// trial generation's own hook does not account it twice.
    pub chain_in_flight: bool,
}

impl Trial {
    pub fn generation(&self) -> Generation {
        Generation {
            slot: self.slot.clone(),
            version: self.version,
            bytes: self.bytes,
            sha256: self.sha256.clone(),
            state_format: self.state_format,
            generation_id: self.generation_id.clone(),
            min_reader_state_format: self.min_reader_state_format,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryEntry {
    pub action: String,
    pub slot: String,
    pub version: u64,
    pub at: i64,
    pub result: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SlotMap {
    #[serde(rename = "A")]
    pub a: Option<Generation>,
    #[serde(rename = "B")]
    pub b: Option<Generation>,
}

impl SlotMap {
    pub fn get(&self, slot: &str) -> &Option<Generation> {
        if slot == "A" { &self.a } else { &self.b }
    }
    pub fn set(&mut self, generation: Generation) {
        if generation.slot == "A" {
            self.a = Some(generation);
        } else {
            self.b = Some(generation);
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Control {
    pub schema: String,
    pub confirmed: Generation,
    pub trial: Option<Trial>,
    pub slots: SlotMap,
    pub history: Vec<HistoryEntry>,
}

impl Control {
    pub fn validate(&self, layout: &Layout) -> io::Result<()> {
        if self.schema != CONTROL_SCHEMA {
            return Err(err("invalid control schema"));
        }
        let check_gen = |g: &Generation| -> io::Result<()> {
            if g.slot != "A" && g.slot != "B" {
                return Err(err("invalid generation slot"));
            }
            if g.version < 1 || g.bytes == 0 || g.bytes > layout.slot(&g.slot).bytes() {
                return Err(err("invalid generation size"));
            }
            if g.sha256.len() != 64 || !g.sha256.chars().all(|c| c.is_ascii_hexdigit()) {
                return Err(err("invalid generation digest"));
            }
            if g.state_format < 1 {
                return Err(err("invalid generation state format"));
            }
            if g.generation_id.is_empty()
                || g.generation_id.len() > 64
                || !g
                    .generation_id
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-')
            {
                return Err(err("invalid generation id"));
            }
            if g.min_reader_state_format < 1 || g.min_reader_state_format > g.state_format {
                return Err(err("invalid generation reader floor"));
            }
            Ok(())
        };
        check_gen(&self.confirmed)?;
        for slot in ["A", "B"] {
            if let Some(g) = self.slots.get(slot) {
                check_gen(g)?;
                if g.slot != slot {
                    return Err(err("slot map identity mismatch"));
                }
            }
        }
        match self.slots.get(&self.confirmed.slot) {
            Some(g) if g.version == self.confirmed.version => {}
            _ => return Err(err("confirmed generation missing from slot map")),
        }
        if let Some(trial) = &self.trial {
            check_gen(&trial.generation())?;
            if trial.slot == self.confirmed.slot {
                return Err(err("trial targets the confirmed slot"));
            }
            if trial.attempts > TRIAL_ATTEMPTS {
                return Err(err("invalid trial attempts"));
            }
        }
        if self.history.len() > MAX_HISTORY {
            return Err(err("history too long"));
        }
        Ok(())
    }

    pub fn push_history(&mut self, action: &str, slot: &str, version: u64, result: &str) {
        self.history.push(HistoryEntry {
            action: action.to_string(),
            slot: slot.to_string(),
            version,
            at: now(),
            result: result.to_string(),
        });
        if self.history.len() > MAX_HISTORY {
            self.history.drain(..self.history.len() - MAX_HISTORY);
        }
    }
}

pub fn atomic_write_json(path: &Path, value: &Value) -> io::Result<()> {
    let tmp = path.with_extension("json.tmp");
    let text =
        serde_json::to_string_pretty(value).map_err(|_| err("record serialization failed"))?;
    {
        let mut file = fs::File::create(&tmp)?;
        file.write_all(text.as_bytes())?;
        file.write_all(b"\n")?;
        file.sync_all()?;
    }
    fs::rename(&tmp, path)?;
    if let Some(parent) = path.parent() {
        sync_dir(parent)?;
    }
    Ok(())
}

pub fn load_control(layout: &Layout) -> io::Result<Control> {
    let raw = fs::read_to_string(CONTROL_PATH)?;
    let control: Control =
        serde_json::from_str(&raw).map_err(|_| err("control record does not parse"))?;
    control.validate(layout)?;
    Ok(control)
}

pub fn save_control(control: &Control) -> io::Result<()> {
    let value = serde_json::to_value(control).map_err(|_| err("record encode failed"))?;
    atomic_write_json(Path::new(CONTROL_PATH), &value)
}

// ---------------------------------------------------------------------
// Trust anchor and generation stamp (both provisioned at build time)

#[derive(Clone, Debug)]
pub struct Trust {
    pub key_id: String,
    pub development: bool,
    pub public_key: [u8; 32],
}

pub fn load_trust() -> io::Result<Trust> {
    let raw = fs::read_to_string(TRUST_PATH)?;
    let value: Value = serde_json::from_str(&raw).map_err(|_| err("trust record parse"))?;
    let obj = value.as_object().ok_or_else(|| err("trust record shape"))?;
    if obj.len() != 4
        || obj.get("schema").and_then(Value::as_str) != Some(TRUST_SCHEMA)
        || obj.get("development").and_then(Value::as_bool).is_none()
    {
        return Err(err("invalid publisher trust record"));
    }
    let key_id = obj
        .get("key_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| err("invalid publisher key id"))?
        .to_string();
    let hex_key = obj
        .get("public_key_hex")
        .and_then(Value::as_str)
        .ok_or_else(|| err("missing pinned public key"))?;
    if hex_key.len() != 64 {
        return Err(err("pinned public key has the wrong length"));
    }
    let mut public_key = [0u8; 32];
    for i in 0..32 {
        public_key[i] = u8::from_str_radix(&hex_key[i * 2..i * 2 + 2], 16)
            .map_err(|_| err("pinned public key is not hex"))?;
    }
    Ok(Trust {
        key_id,
        development: obj["development"].as_bool().unwrap_or(false),
        public_key,
    })
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stamp {
    pub schema: String,
    pub version: u64,
    pub commit: String,
    pub state_format: u64,
    pub generation_id: String,
}

pub fn load_stamp() -> io::Result<Stamp> {
    let raw = fs::read_to_string(STAMP_PATH)?;
    let stamp: Stamp =
        serde_json::from_str(&raw).map_err(|_| err("generation stamp does not parse"))?;
    if stamp.schema != STAMP_SCHEMA
        || stamp.version < 1
        || stamp.state_format < 1
        || stamp.generation_id.is_empty()
        || stamp.generation_id.len() > 64
        || !stamp
            .generation_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err(err("invalid generation stamp"));
    }
    Ok(stamp)
}

// ---------------------------------------------------------------------
// Slot contents

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SlotIdentity {
    pub schema: String,
    pub slot: String,
    pub version: u64,
    pub bytes: u64,
    pub sha256: String,
    pub state_format: u64,
    pub generation_id: String,
    pub min_reader_state_format: u64,
}

impl SlotIdentity {
    pub fn generation(&self) -> Generation {
        Generation {
            slot: self.slot.clone(),
            version: self.version,
            bytes: self.bytes,
            sha256: self.sha256.clone(),
            state_format: self.state_format,
            generation_id: self.generation_id.clone(),
            min_reader_state_format: self.min_reader_state_format,
        }
    }
}

pub fn digest_file(path: &Path) -> io::Result<(String, u64)> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    let mut total = 0u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        total += read as u64;
    }
    Ok((hex(&hasher.finalize()), total))
}

/// Mount a slot read-only and read its identity record, if any.
pub fn read_slot_identity(layout: &Layout, slot: &str) -> io::Result<Option<SlotIdentity>> {
    let mount = Mount::attach(layout.slot(slot), "vfat", &format!("slot-{slot}"), true)?;
    let path = mount.path().join(GEN_JSON_PATH);
    if !path.is_file() {
        return Ok(None);
    }
    let raw = fs::read_to_string(&path)?;
    let identity: SlotIdentity =
        serde_json::from_str(&raw).map_err(|_| err("slot identity does not parse"))?;
    if identity.schema != SLOT_IDENTITY_SCHEMA || identity.slot != slot {
        return Err(err("slot identity mismatch"));
    }
    Ok(Some(identity))
}

/// Mount a slot read-only and hash the generation image it holds.
pub fn hash_slot_image(layout: &Layout, slot: &str) -> io::Result<(String, u64)> {
    let mount = Mount::attach(layout.slot(slot), "vfat", &format!("slot-{slot}"), true)?;
    digest_file(&mount.path().join(GEN_EFI_PATH))
}

// ---------------------------------------------------------------------
// Provisioning (first boot of an assembled A/B disk)

pub fn provision(layout: &Layout, stamp: &Stamp) -> io::Result<Control> {
    // The assembled disk's only pre-provisioned generation is slot
    // A's; the running image must be that generation. Its identity
    // comes from the build-time stamp matched against slot A's
    // on-disk identity record — never from firmware state.
    let identity_a = read_slot_identity(layout, "A")?
        .ok_or_else(|| err("slot A holds no generation identity"))?;
    if identity_a.generation_id != stamp.generation_id || identity_a.version != stamp.version {
        return Err(err(
            "running generation does not match slot A at provisioning",
        ));
    }
    let identity_b = read_slot_identity(layout, "B")?;
    let gen_a = identity_a.generation();
    let gen_b = identity_b.map(|i| i.generation());
    let mut control = Control {
        schema: CONTROL_SCHEMA.into(),
        confirmed: gen_a.clone(),
        trial: None,
        slots: SlotMap {
            a: Some(gen_a),
            b: gen_b,
        },
        history: Vec::new(),
    };
    control.push_history("provision", "A", identity_a.version, "confirmed");
    save_control(&control)?;
    marker(&format!(
        "SYNVEIL_AB_PROVISIONED slot=A version={} generation_id={}",
        identity_a.version, identity_a.generation_id
    ));
    Ok(control)
}

// ---------------------------------------------------------------------
// Chain loading (kexec): the only slot-selection mechanism. The
// running generation loads the desired slot's image into the kernel
// and reboots into it; no firmware state is read or written.

#[cfg(target_arch = "x86_64")]
unsafe fn syscall5(nr: i64, a1: u64, a2: u64, a3: u64, a4: u64, a5: u64) -> i64 {
    let ret: i64;
    unsafe {
        core::arch::asm!(
            "syscall",
            inlateout("rax") nr => ret,
            in("rdi") a1,
            in("rsi") a2,
            in("rdx") a3,
            in("r10") a4,
            in("r8") a5,
            lateout("rcx") _,
            lateout("r11") _,
            options(nostack)
        );
    }
    ret
}

const KEXEC_FILE_NO_INITRAMFS: u64 = 0x4;
const LINUX_REBOOT_MAGIC1: u64 = 0xfee1_dead;
const LINUX_REBOOT_MAGIC2: u64 = 0x2812_1969;
const LINUX_REBOOT_CMD_KEXEC: u64 = 0x4558_4543;

/// Verify the target slot's bytes against the record, load them as
/// the next kernel, and reboot into them. Returns only on failure:
/// a successful call never comes back.
pub fn chain_load(layout: &Layout, generation: &Generation) -> io::Result<()> {
    let mount = Mount::attach(layout.slot(&generation.slot), "vfat", "chain", true)?;
    let image = mount.path().join(GEN_EFI_PATH);
    let (digest, size) = digest_file(&image)?;
    if digest != generation.sha256 || size != generation.bytes {
        return Err(err("chain target digest mismatch; refusing transfer"));
    }
    marker(&format!(
        "SYNVEIL_AB_CHAIN_LOAD slot={} version={} generation_id={}",
        generation.slot, generation.version, generation.generation_id
    ));
    #[cfg(target_arch = "x86_64")]
    {
        use std::os::unix::io::AsRawFd;
        let file = fs::File::open(&image)?;
        // The generation kernels carry CMDLINE_OVERRIDE, so the
        // passed command line is ignored; pass the running one for
        // want of anything better defined. kexec_file_load still
        // validates it: the buffer must be NUL-terminated with the
        // length including the NUL (kernel/kexec_file.c), which
        // /proc/cmdline is not — terminate it explicitly.
        let mut cmdline = fs::read("/proc/cmdline").unwrap_or_default();
        while matches!(cmdline.last(), Some(b'\n' | b' ' | b'\0')) {
            cmdline.pop();
        }
        cmdline.push(0);
        let rc = unsafe {
            syscall5(
                320, // kexec_file_load
                file.as_raw_fd() as u64,
                0,
                cmdline.len() as u64,
                cmdline.as_ptr() as u64,
                KEXEC_FILE_NO_INITRAMFS,
            )
        };
        if rc != 0 {
            return Err(io::Error::from_raw_os_error((-rc) as i32));
        }
        let _ = Command::new("/bin/sync").status();
        let rc = unsafe {
            syscall5(
                169, // reboot
                LINUX_REBOOT_MAGIC1,
                LINUX_REBOOT_MAGIC2,
                LINUX_REBOOT_CMD_KEXEC,
                0,
                0,
            )
        };
        Err(io::Error::from_raw_os_error((-rc) as i32))
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        Err(err("chain loading requires x86_64"))
    }
}

/// Fail closed: run the read-only recovery diagnostics if present,
/// then hold forever. The wrong system never runs, no service
/// starts, and nothing is written anywhere.
pub fn fail_closed(reason: &str) -> ! {
    marker(&format!("SYNVEIL_AB_FAIL_CLOSED reason={reason}"));
    let recovery = Path::new("/usr/libexec/synveil/recovery-status");
    if recovery.is_file() {
        let _ = Command::new(recovery).status();
    }
    marker("SYNVEIL_AB_RECOVERY_HOLD");
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

// ---------------------------------------------------------------------
// Early-init accounting hook

pub enum HookOutcome {
    Disabled,
    Unavailable,
    Provisioned,
    ConfirmedBoot {
        slot: String,
    },
    TrialBoot {
        slot: String,
        version: u64,
        attempts_left: u32,
    },
}

/// Mount the DATA partition as /home on A/B generations, so the
/// session's home (and anything the user keeps in it) lives outside
/// the replaceable system slots, next to STATE. Best effort by
/// design: a missing or unmountable DATA partition leaves the
/// initramfs home in place and is reported, never a boot failure —
/// home is user data, not part of the update trust path.
fn mount_home() {
    let Ok(partitions) = scan_partitions() else {
        return;
    };
    let Ok(Some(data)) = find_partition(&partitions, DATA_PARTUUID) else {
        return;
    };
    if let Ok(mounts) = fs::read_to_string("/proc/mounts")
        && mounts.lines().any(|l| l.contains(" /home "))
    {
        return;
    }
    let status = Command::new("/bin/mount")
        .args(["-t", "ext4", &data.device(), "/home"])
        .status();
    match status {
        Ok(status) if status.success() => {
            let home = Path::new("/home/synveil");
            let _ = fs::create_dir_all(home);
            let _ = Command::new("/bin/chown")
                .args(["1000:1000", "/home/synveil"])
                .status();
            let _ = fs::set_permissions(home, fs::Permissions::from_mode(0o700));
            marker("SYNVEIL_AB_HOME_MOUNTED");
        }
        _ => marker("SYNVEIL_AB_HOME_UNAVAILABLE"),
    }
}

/// Verify the running generation's own slot bytes against its
/// record before it is allowed to become the running system. A
/// mismatch fails closed into the recovery diagnostics: a corrupt
/// confirmed (or trial) image never starts services.
fn verify_running_slot(layout: &Layout, generation: &Generation) -> io::Result<()> {
    let (digest, size) = hash_slot_image(layout, &generation.slot)?;
    if digest != generation.sha256 || size != generation.bytes {
        fail_closed("running-generation-digest-mismatch")
    }
    Ok(())
}

/// Chain to `generation`, or deal with the failure: if we are the
/// confirmed generation ourselves, staying is correct; anything
/// else would run the wrong system, so fail closed.
fn chain_or_stay(
    layout: &Layout,
    control: &Control,
    running_id: &str,
    target: &Generation,
) -> io::Result<HookOutcome> {
    match chain_load(layout, target) {
        Ok(()) => unreachable!("chain_load returns only on failure"),
        Err(error) => {
            marker(&format!(
                "SYNVEIL_AB_CHAIN_FAILED slot={} error={error}",
                target.slot
            ));
            if running_id == control.confirmed.generation_id {
                Ok(HookOutcome::ConfirmedBoot {
                    slot: control.confirmed.slot.clone(),
                })
            } else {
                fail_closed("chain-failed-on-non-confirmed-generation")
            }
        }
    }
}

pub fn boot_hook() -> io::Result<HookOutcome> {
    if !Path::new(ENABLED_MARKER).is_file() {
        return Ok(HookOutcome::Disabled);
    }
    let mounts = fs::read_to_string("/proc/mounts")?;
    if !mounts.lines().any(|l| l.contains(" /var ext4 ")) {
        marker("SYNVEIL_AB_UNAVAILABLE reason=no-state");
        return Ok(HookOutcome::Unavailable);
    }
    let layout = Layout::load()?;
    mount_home();
    let stamp = load_stamp()?;
    if !Path::new(CONTROL_PATH).is_file() {
        provision(&layout, &stamp)?;
        return Ok(HookOutcome::Provisioned);
    }
    let mut control = load_control(&layout)?;
    let running = stamp.generation_id.clone();

    // An exhausted trial (attempts spent) completes its fallback
    // here whenever this boot is not the trial generation itself:
    // the record is cleared before anything else. (The trial
    // generation's own final-attempt boot is handled below.)
    if let Some(trial) = &control.trial
        && trial.attempts == 0
        && running != trial.generation_id
    {
        let exhausted = trial.clone();
        control.trial = None;
        control.push_history(
            "trial-exhausted",
            &exhausted.slot,
            exhausted.version,
            "fallback",
        );
        save_control(&control)?;
        marker(&format!(
            "SYNVEIL_AB_TRIAL_EXHAUSTED slot={} version={}",
            exhausted.slot, exhausted.version
        ));
    }

    let Some(trial) = control.trial.clone() else {
        // No trial: the confirmed generation must run.
        if running == control.confirmed.generation_id {
            verify_running_slot(&layout, &control.confirmed.clone())?;
            marker(&format!(
                "SYNVEIL_AB_CONFIRMED_BOOT slot={} version={}",
                control.confirmed.slot, control.confirmed.version
            ));
            return Ok(HookOutcome::ConfirmedBoot {
                slot: control.confirmed.slot.clone(),
            });
        }
        let target = control.confirmed.clone();
        return chain_or_stay(&layout, &control, &running, &target);
    };

    if running == trial.generation_id {
        // We ARE the trial generation.
        if trial.chain_in_flight {
            // The chaining generation already accounted this boot
            // before the transfer; just clear the flag.
            verify_running_slot(&layout, &trial.generation())?;
            let mut control = control;
            let trial = control.trial.as_mut().expect("trial present");
            trial.chain_in_flight = false;
            let left = trial.attempts;
            let version = trial.version;
            let slot = trial.slot.clone();
            save_control(&control)?;
            marker(&format!(
                "SYNVEIL_AB_TRIAL_BOOT slot={slot} version={version} attempts_left={left}"
            ));
            return Ok(HookOutcome::TrialBoot {
                slot,
                version,
                attempts_left: left,
            });
        }
        if trial.attempts == 0 {
            // Directly booted with no attempts left: expire and hand
            // over to the confirmed generation.
            let mut control = control;
            control.trial = None;
            control.push_history("trial-exhausted", &trial.slot, trial.version, "fallback");
            save_control(&control)?;
            marker(&format!(
                "SYNVEIL_AB_TRIAL_EXHAUSTED slot={} version={}",
                trial.slot, trial.version
            ));
            let target = control.confirmed.clone();
            return chain_or_stay(&layout, &control, &running, &target);
        }
        // Direct boot of the trial (e.g. an owner menu pick): account
        // it here, before services, like any trial boot.
        verify_running_slot(&layout, &trial.generation())?;
        let mut control = control;
        let trial = control.trial.as_mut().expect("trial present");
        trial.attempts -= 1;
        trial.awaiting_health = true;
        let left = trial.attempts;
        let version = trial.version;
        let slot = trial.slot.clone();
        save_control(&control)?;
        marker(&format!(
            "SYNVEIL_AB_TRIAL_BOOT slot={slot} version={version} attempts_left={left}"
        ));
        return Ok(HookOutcome::TrialBoot {
            slot,
            version,
            attempts_left: left,
        });
    }

    // A trial is pending and we are not it: account the boot and
    // hand over. The record is durable BEFORE the transfer.
    {
        let trial = control.trial.as_mut().expect("trial present");
        trial.attempts -= 1;
        trial.awaiting_health = true;
        trial.chain_in_flight = true;
    }
    save_control(&control)?;
    marker(&format!(
        "SYNVEIL_AB_TRIAL_HANDOVER slot={} version={} attempts_left={}",
        trial.slot, trial.version, trial.attempts
    ));
    chain_or_stay(&layout, &control, &running, &trial.generation())
}

/// Reboot immediately (used for trial exhaustion, before services).
pub fn reboot_now() -> ! {
    let _ = Command::new("/bin/sync").status();
    let _ = Command::new("/sbin/reboot").arg("-f").status();
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

// ---------------------------------------------------------------------
// Release verification and staging

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReleaseManifest {
    schema: String,
    version: u64,
    bytes: u64,
    sha256: String,
    generation_id: String,
    key_id: String,
    issued_at: i64,
    expires_at: i64,
    state_format: u64,
    min_reader_state_format: u64,
    rollback_breaking: bool,
}

fn verify_release(dir: &Path, trust: &Trust, at: i64) -> io::Result<(ReleaseManifest, Vec<u8>)> {
    for planted in ["publisher.pem", "publisher.json", "fixture-trust.pem"] {
        if dir.join(planted).exists() {
            return Err(err("release directory carries trust material; refused"));
        }
    }
    let raw = fs::read(dir.join("synveil.release.json"))?;
    if raw.len() > 64 * 1024 {
        return Err(err("release metadata too large"));
    }
    let manifest: ReleaseManifest =
        serde_json::from_slice(&raw).map_err(|_| err("invalid release manifest"))?;
    if manifest.schema != RELEASE_SCHEMA
        || manifest.version < 1
        || manifest.bytes == 0
        || manifest.bytes > MAX_RELEASE_BYTES
        || manifest.sha256.len() != 64
        || !manifest.sha256.chars().all(|c| c.is_ascii_hexdigit())
        || manifest.key_id != trust.key_id
        || manifest.generation_id.is_empty()
        || manifest.generation_id.len() > 64
        || !manifest
            .generation_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
        || manifest.state_format < 1
        || manifest.min_reader_state_format < 1
        || manifest.min_reader_state_format > manifest.state_format
    {
        return Err(err("invalid release manifest"));
    }
    if at < manifest.issued_at {
        return Err(err("release metadata not yet valid"));
    }
    if at > manifest.expires_at {
        return Err(err("release metadata expired"));
    }
    if manifest.rollback_breaking {
        return Err(err(
            "rollback-breaking releases are refused in this milestone",
        ));
    }
    let sig_raw = fs::read(dir.join("synveil.release.sig"))?;
    if sig_raw.len() != 64 {
        return Err(err("release signature has the wrong length"));
    }
    let mut signature = [0u8; 64];
    signature.copy_from_slice(&sig_raw);
    if !ed25519::verify(&trust.public_key, &signature, &raw) {
        return Err(err("release signature verification failed"));
    }
    Ok((manifest, raw))
}

pub fn stage() -> io::Result<Value> {
    let layout = Layout::load()?;
    let control = load_control(&layout)?;
    if control.trial.is_some() {
        return Err(err("trial already pending; decide or roll back first"));
    }
    // Never build on a corrupt base: the confirmed slot must still
    // hash to the record before anything is staged.
    let (digest, size) = hash_slot_image(&layout, &control.confirmed.slot)?;
    if digest != control.confirmed.sha256 || size != control.confirmed.bytes {
        return Err(err("confirmed system slot corruption: refusing operation"));
    }
    let trust = load_trust()?;
    let partitions = scan_partitions()?;
    let media = find_partition(&partitions, RELEASE_PARTUUID)?
        .ok_or_else(|| err("no release media attached"))?;
    let media_mount = Mount::attach(&media, "ext4", "release", true)?;
    let (manifest, _raw) = verify_release(media_mount.path(), &trust, now())?;
    if manifest.version <= control.confirmed.version {
        return Err(err("replay/downgrade refused: version not above confirmed"));
    }
    if manifest.min_reader_state_format > control.confirmed.state_format {
        return Err(err(
            "migration refused: candidate cannot read current state",
        ));
    }
    if manifest.state_format < control.confirmed.state_format {
        return Err(err("migration refused: candidate would strand newer state"));
    }
    // Rollback direction (the Pass A defect): after the candidate
    // runs, STATE will be at the candidate's format, and the
    // confirmed generation — the rollback target — must still be
    // able to read it.
    if !(control.confirmed.min_reader_state_format <= manifest.state_format
        && manifest.state_format <= control.confirmed.state_format)
    {
        return Err(err(
            "migration refused: confirmed generation could not read state after candidate writes (rollback unsafe)",
        ));
    }
    let payload = media_mount.path().join("synveil.release.bin");
    let (payload_digest, payload_size) = digest_file(&payload)?;
    if payload_digest != manifest.sha256 || payload_size != manifest.bytes {
        return Err(err("release payload hash mismatch"));
    }
    let inactive = if control.confirmed.slot == "A" {
        "B"
    } else {
        "A"
    };
    if manifest.bytes > layout.slot(inactive).bytes() {
        return Err(err("signed release exceeds inactive slot"));
    }
    marker(&format!(
        "SYNVEIL_AB_STAGE_COPYING slot={inactive} version={} bytes={}",
        manifest.version, manifest.bytes
    ));
    {
        let slot = Mount::attach(layout.slot(inactive), "vfat", "slot-write", false)?;
        let efi_dir = slot.path().join("EFI/synveil");
        fs::create_dir_all(&efi_dir)?;
        let tmp = efi_dir.join("gen.efi.tmp");
        {
            let mut source = fs::File::open(&payload)?;
            let mut target = fs::File::create(&tmp)?;
            std::io::copy(&mut source, &mut target)?;
            target.sync_all()?;
        }
        let target_efi = efi_dir.join("gen.efi");
        fs::rename(&tmp, &target_efi)?;
        let identity = SlotIdentity {
            schema: SLOT_IDENTITY_SCHEMA.into(),
            slot: inactive.into(),
            version: manifest.version,
            bytes: manifest.bytes,
            sha256: manifest.sha256.clone(),
            state_format: manifest.state_format,
            generation_id: manifest.generation_id.clone(),
            min_reader_state_format: manifest.min_reader_state_format,
        };
        let text = serde_json::to_string_pretty(&identity)
            .map_err(|_| err("slot identity encode failed"))?;
        let mut file = fs::File::create(efi_dir.join("gen.json"))?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
        sync_dir(&efi_dir)?;
    }
    // Re-read the staged bytes from the slot itself before any
    // record points at them.
    let (slot_digest, slot_size) = hash_slot_image(&layout, inactive)?;
    if slot_digest != manifest.sha256 || slot_size != manifest.bytes {
        return Err(err("inactive-slot write verification failed"));
    }
    let mut control = control;
    let trial = Trial {
        slot: inactive.into(),
        version: manifest.version,
        bytes: manifest.bytes,
        sha256: manifest.sha256.clone(),
        state_format: manifest.state_format,
        generation_id: manifest.generation_id.clone(),
        min_reader_state_format: manifest.min_reader_state_format,
        attempts: TRIAL_ATTEMPTS,
        awaiting_health: false,
        chain_in_flight: false,
    };
    // The slot map records what each slot currently holds.
    control.slots.set(trial.generation());
    control.trial = Some(trial);
    control.push_history("stage", inactive, manifest.version, "trial");
    save_control(&control)?;
    // Accounting is durable. Nothing is pointed at the candidate:
    // the next boot's init hook sees the trial record and
    // chain-loads it (see boot_hook).
    marker(&format!(
        "SYNVEIL_AB_STAGED slot={inactive} version={} key_id={} development={}",
        manifest.version, trust.key_id, trust.development
    ));
    Ok(json!({
        "staged_slot": inactive,
        "version": manifest.version,
        "trial_attempts": TRIAL_ATTEMPTS,
        "publisher_key_id": trust.key_id,
        "publisher_development_key": trust.development,
        "restart_required": true,
    }))
}

pub fn rollback() -> io::Result<Value> {
    let layout = Layout::load()?;
    let mut control = load_control(&layout)?;
    if control.trial.is_some() {
        let trial = control.trial.take().expect("trial present");
        // A trial that already booted may have written STATE at its
        // own format; discarding it is safe only if the confirmed
        // generation can read that format.
        if trial.awaiting_health
            && !(control.confirmed.min_reader_state_format <= trial.state_format
                && trial.state_format <= control.confirmed.state_format)
        {
            return Err(err(
                "rollback refused: trial may have advanced state beyond the confirmed generation's readable range",
            ));
        }
        let confirmed = control.confirmed.clone();
        control.push_history(
            "rollback",
            &confirmed.slot,
            confirmed.version,
            "trial-discarded",
        );
        save_control(&control)?;
        marker("SYNVEIL_AB_ROLLBACK result=trial-discarded");
        return Ok(json!({"selected_slot": confirmed.slot, "reason": "pending-trial-discarded"}));
    }
    let other = if control.confirmed.slot == "A" {
        "B"
    } else {
        "A"
    };
    let retained = control
        .slots
        .get(other)
        .clone()
        .ok_or_else(|| err("no retained generation to roll back to"))?;
    let (digest, size) = hash_slot_image(&layout, other)?;
    if digest != retained.sha256 || size != retained.bytes {
        return Err(err("retained generation is corrupt; rollback refused"));
    }
    // The retained generation must be able to read STATE as the
    // confirmed generation leaves it.
    if !(retained.min_reader_state_format <= control.confirmed.state_format
        && control.confirmed.state_format <= retained.state_format)
    {
        return Err(err(
            "rollback refused: retained generation cannot read the current state format",
        ));
    }
    control.trial = Some(Trial {
        slot: retained.slot.clone(),
        version: retained.version,
        bytes: retained.bytes,
        sha256: retained.sha256.clone(),
        state_format: retained.state_format,
        generation_id: retained.generation_id.clone(),
        min_reader_state_format: retained.min_reader_state_format,
        attempts: TRIAL_ATTEMPTS,
        awaiting_health: false,
        chain_in_flight: false,
    });
    let version = retained.version;
    control.push_history("rollback", other, version, "trial");
    save_control(&control)?;
    marker(&format!(
        "SYNVEIL_AB_ROLLBACK result=trial slot={other} version={version}"
    ));
    Ok(json!({"selected_slot": other, "reason": "owner-rollback-trial", "version": version}))
}

pub fn status() -> io::Result<Value> {
    let stamp = load_stamp().ok();
    let trust = load_trust().ok();
    let layout = Layout::load();
    let control = match &layout {
        Ok(layout) => load_control(layout).ok(),
        Err(_) => None,
    };
    let booted = match (&stamp, &control) {
        (Some(stamp), Some(control)) => {
            if stamp.generation_id == control.confirmed.generation_id {
                Some(control.confirmed.slot.clone())
            } else if control
                .trial
                .as_ref()
                .is_some_and(|t| t.generation_id == stamp.generation_id)
            {
                control.trial.as_ref().map(|t| t.slot.clone())
            } else {
                Some("entry".to_string())
            }
        }
        _ => None,
    };
    Ok(json!({
        "enabled": Path::new(ENABLED_MARKER).is_file(),
        "running": stamp.map(|s| json!({"version": s.version, "commit": s.commit,
                                        "state_format": s.state_format,
                                        "generation_id": s.generation_id})),
        "publisher": trust.map(|t| json!({"key_id": t.key_id, "development": t.development})),
        "booted_slot": booted,
        "confirmed": control.as_ref().map(|c| serde_json::to_value(&c.confirmed).unwrap_or(Value::Null)),
        "trial": control.as_ref().and_then(|c| c.trial.as_ref())
            .map(|t| serde_json::to_value(t).unwrap_or(Value::Null)),
        "slots": control.as_ref().map(|c| serde_json::to_value(&c.slots).unwrap_or(Value::Null)),
        "health": health_json(),
        "available": available_release(),
        "history": control.as_ref().map(|c| serde_json::to_value(&c.history).unwrap_or(Value::Null)),
    }))
}

/// Probe the release medium (if attached) for the status report:
/// the offered generation and whether its signature verifies
/// against the pinned publisher key. Verification here is the same
/// verify_release the stage action uses — metadata signature and
/// validity window only; the payload digest is checked at stage
/// time, before anything is written. A probe failure is reported
/// as data, never as a status failure.
fn available_release() -> Value {
    let Ok(partitions) = scan_partitions() else {
        return json!({"attached": false});
    };
    let Ok(Some(media)) = find_partition(&partitions, RELEASE_PARTUUID) else {
        return json!({"attached": false});
    };
    let Ok(trust) = load_trust() else {
        return json!({"attached": true, "error": "publisher trust unavailable"});
    };
    let Ok(mount) = Mount::attach(&media, "ext4", "release-probe", true) else {
        return json!({"attached": true, "error": "release media unreadable"});
    };
    match verify_release(mount.path(), &trust, now()) {
        Ok((manifest, _raw)) => json!({
            "attached": true,
            "version": manifest.version,
            "generation_id": manifest.generation_id,
            "bytes": manifest.bytes,
            "signature": "valid",
        }),
        Err(error) => json!({"attached": true, "error": error.to_string()}),
    }
}

// ---------------------------------------------------------------------
// Health confirmation (runs in the agent daemon after boot)

fn probe_credential() -> bool {
    let path = Path::new(crate::credential::ROOT_CREDENTIAL_PATH);
    let Ok(raw) = fs::read_to_string(path) else {
        return false;
    };
    // Parse with the same PHC machinery the verifier uses; a full
    // password verification happens at the graphical login.
    argon2::PasswordHash::new(raw.trim()).is_ok()
}

fn probe_core() -> bool {
    let Ok(mut stream) = UnixStream::connect(crate::DEFAULT_SOCKET_PATH) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = veil_protocol::Request::new("ab-health", "status", json!({}));
    let Ok(line) = veil_protocol::encode_line(&request) else {
        return false;
    };
    if stream.write_all(&line).is_err() {
        return false;
    }
    let mut reader = std::io::BufReader::new(stream);
    let Ok(Some(frame)) = veil_protocol::read_frame(&mut reader) else {
        return false;
    };
    veil_protocol::decode_response(&frame)
        .map(|r| r.ok)
        .unwrap_or(false)
}

fn probe_session() -> bool {
    let runtime = Path::new("/run/user/1000");
    // The session's Wayland socket is named by weston-session
    // (WAYLAND_DISPLAY=synveil-0); there is no wayland-* socket.
    let wayland = fs::read_dir(runtime)
        .map(|entries| {
            entries.flatten().any(|e| {
                e.file_name() == "synveil-0"
                    && e.file_type().map(|t| t.is_socket()).unwrap_or(false)
            })
        })
        .unwrap_or(false);
    if !wayland {
        return false;
    }
    let mut weston = false;
    let mut seat = false;
    if let Ok(procs) = fs::read_dir("/proc") {
        for entry in procs.flatten() {
            let Ok(comm) = fs::read_to_string(entry.path().join("comm")) else {
                continue;
            };
            match comm.trim() {
                "weston" => weston = true,
                "veil-seat" => seat = true,
                _ => {}
            }
        }
    }
    weston && seat
}

/// Health outcome for this boot's trial, assessed once by the
/// agent's health thread and consulted by the `confirm` action.
/// Promotion is two-key: health passing here is necessary but never
/// sufficient — the owner confirms (see the module docs).
#[derive(Clone, Copy, PartialEq, Eq)]
enum HealthPhase {
    /// No trial is being assessed on this boot.
    None,
    Checking,
    Passed,
    Failed,
}

static HEALTH_PHASE: std::sync::Mutex<HealthPhase> = std::sync::Mutex::new(HealthPhase::None);
static HEALTH_CHECK: std::sync::Mutex<Option<&'static str>> = std::sync::Mutex::new(None);

fn set_health(phase: HealthPhase, check: Option<&'static str>) {
    if let Ok(mut slot) = HEALTH_PHASE.lock() {
        *slot = phase;
    }
    if let Ok(mut slot) = HEALTH_CHECK.lock() {
        *slot = check;
    }
}

fn health_state() -> (HealthPhase, Option<&'static str>) {
    let phase = HEALTH_PHASE
        .lock()
        .map(|slot| *slot)
        .unwrap_or(HealthPhase::None);
    let check = HEALTH_CHECK.lock().map(|slot| *slot).unwrap_or(None);
    (phase, check)
}

fn health_json() -> Value {
    let (phase, check) = health_state();
    json!({
        "phase": match phase {
            HealthPhase::None => "none",
            HealthPhase::Checking => "checking",
            HealthPhase::Passed => "passed",
            HealthPhase::Failed => "failed",
        },
        "check": check,
    })
}

enum HealthChecks {
    Passed,
    Failed(&'static str),
}

/// Run the trial health checks (STATE read-write, credential record
/// parses, veil-core answers, graphical session live, booted slot
/// still hashes to the staged digest). The running generation must
/// be the staged one: its build-time stamp agrees with the trial
/// record — no firmware state is consulted.
fn run_health_checks(layout: &Layout, trial: &Trial) -> HealthChecks {
    let stamp = match load_stamp() {
        Ok(stamp) => stamp,
        Err(_) => return HealthChecks::Failed("identity"),
    };
    if stamp.version != trial.version || stamp.generation_id != trial.generation_id {
        marker(&format!(
            "SYNVEIL_AB_HEALTH_FAILED check=identity stamp_version={} trial_version={}",
            stamp.version, trial.version
        ));
        return HealthChecks::Failed("identity");
    }
    // Generous under emulation: the session can only appear after a
    // graphical login, and a slow first login must not fail a healthy
    // candidate. A genuinely unhealthy candidate still fails every
    // check for the whole window, on each of its attempts.
    let deadline = std::time::Instant::now() + Duration::from_secs(300);
    let mut identity_checked = false;
    loop {
        let mounts = fs::read_to_string("/proc/mounts").unwrap_or_default();
        let state_rw = mounts
            .lines()
            .find(|l| l.contains(" /var ext4 "))
            .map(|l| l.split_whitespace().nth(3).unwrap_or("").starts_with("rw"))
            .unwrap_or(false);
        let mut failing = None;
        if !state_rw {
            failing = Some("state-rw");
        } else if !probe_credential() {
            failing = Some("credential");
        } else if !probe_core() {
            failing = Some("core");
        } else if !probe_session() {
            failing = Some("session");
        } else if !identity_checked {
            match hash_slot_image(layout, &trial.slot) {
                Ok((digest, size)) if digest == trial.sha256 && size == trial.bytes => {
                    identity_checked = true;
                }
                _ => failing = Some("slot-digest"),
            }
        }
        if failing.is_none() && identity_checked {
            return HealthChecks::Passed;
        }
        if std::time::Instant::now() > deadline {
            marker(&format!(
                "SYNVEIL_AB_HEALTH_FAILED check={}",
                failing.unwrap_or("timeout")
            ));
            return HealthChecks::Failed(failing.unwrap_or("timeout"));
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// If this boot is a trial awaiting health, run the health checks
/// and record the outcome for the owner's `confirm` action. Returns
/// a short outcome token; the caller reboots on "health-failed".
/// A passing trial is NOT promoted here — promotion is the owner's
/// explicit act (module docs, docs/AB_UPDATER.md).
pub fn health_assessment() -> io::Result<&'static str> {
    let layout = Layout::load()?;
    let control = load_control(&layout)?;
    let Some(trial) = &control.trial else {
        return Ok("no-trial");
    };
    if !trial.awaiting_health {
        return Ok("trial-not-booted");
    }
    // The running generation identifies itself by its build-time
    // stamp; no firmware state is consulted.
    let stamp = load_stamp()?;
    if stamp.generation_id != trial.generation_id {
        return Ok("trial-not-booted");
    }
    set_health(HealthPhase::Checking, None);
    match run_health_checks(&layout, trial) {
        HealthChecks::Passed => {
            set_health(HealthPhase::Passed, None);
            marker(&format!(
                "SYNVEIL_AB_HEALTH_PASSED slot={} version={} awaiting=owner-confirm",
                trial.slot, trial.version
            ));
            Ok("awaiting-confirm")
        }
        HealthChecks::Failed(check) => {
            set_health(HealthPhase::Failed, Some(check));
            Ok("health-failed")
        }
    }
}

/// Owner confirmation of a booted trial (typed action `confirm`).
/// Promotes the trial to confirmed only when this boot's health
/// checks passed, the running system IS the trial generation, and
/// the candidate's slot bytes still hash to the trial record at
/// confirm time. Every refusal names its reason; nothing here can
/// promote an unhealthy or substituted candidate.
pub fn confirm() -> io::Result<Value> {
    let layout = Layout::load()?;
    let control = load_control(&layout)?;
    let Some(trial) = control.trial.clone() else {
        return Err(err("no trial update to confirm"));
    };
    if !trial.awaiting_health {
        return Err(err("the trial has not booted yet; restart to try it first"));
    }
    let stamp = load_stamp()?;
    if stamp.generation_id != trial.generation_id {
        return Err(err("the running system is not the trial generation"));
    }
    match health_state() {
        (HealthPhase::Passed, _) => {}
        (HealthPhase::Checking, _) => {
            return Err(err("health checks are still running for this trial"));
        }
        (HealthPhase::Failed, check) => {
            return Err(err(&format!(
                "health checks failed for this trial (check: {}); it cannot be confirmed",
                check.unwrap_or("unknown")
            )));
        }
        (HealthPhase::None, _) => {
            return Err(err("health checks have not passed for this trial"));
        }
    }
    let (digest, size) = hash_slot_image(&layout, &trial.slot)?;
    if digest != trial.sha256 || size != trial.bytes {
        return Err(err("candidate slot digest mismatch; confirmation refused"));
    }
    // Reload so a record changed since the assessment is never
    // overwritten blind: the trial on disk must still be the one
    // whose health passed.
    let mut control = load_control(&layout)?;
    match &control.trial {
        Some(current)
            if current.generation_id == trial.generation_id && current.sha256 == trial.sha256 => {}
        _ => return Err(err("the trial record changed; confirmation refused")),
    }
    let trial = control.trial.take().expect("trial present");
    let promoted = trial.generation();
    control.slots.set(promoted.clone());
    control.confirmed = promoted.clone();
    control.push_history("confirm", &promoted.slot, promoted.version, "confirmed");
    save_control(&control)?;
    marker(&format!(
        "SYNVEIL_AB_CONFIRMED slot={} version={}",
        promoted.slot, promoted.version
    ));
    Ok(json!({
        "confirmed_slot": promoted.slot,
        "version": promoted.version,
        "generation_id": promoted.generation_id,
    }))
}

// ---------------------------------------------------------------------
// Agent daemon + client (typed privilege boundary, schema
// synveil.ab-agent/v1: newline-delimited JSON over a unix socket
// owned by root, group synveil, mode 0660; actions: status, stage,
// confirm, rollback, reboot — nothing else exists)

pub const AGENT_SCHEMA: &str = "synveil.ab-agent/v1";
const MAX_REQUEST_BYTES: usize = 4096;

fn dispatch(action: &str) -> Value {
    let result = match action {
        "status" => status(),
        "stage" => stage(),
        "confirm" => confirm(),
        "rollback" => rollback(),
        "reboot" => {
            std::thread::spawn(|| {
                std::thread::sleep(Duration::from_secs(1));
                reboot_now();
            });
            Ok(json!({"rebooting": true}))
        }
        _ => Err(err("unknown action")),
    };
    match result {
        Ok(value) => json!({"ok": true, "result": value}),
        Err(error) => {
            marker(&format!(
                "SYNVEIL_AB_ACTION_FAILED action={action} error={error}"
            ));
            json!({"ok": false, "error": error.to_string()})
        }
    }
}

pub fn serve() -> io::Result<()> {
    fs::create_dir_all(RUNTIME_DIR)?;
    fs::set_permissions(RUNTIME_DIR, fs::Permissions::from_mode(0o750))?;
    let _ = Command::new("/bin/chown")
        .args(["0:1000", RUNTIME_DIR])
        .status();
    let socket = Path::new(SOCKET_PATH);
    if socket.exists() {
        fs::remove_file(socket)?;
    }
    let health = std::thread::spawn(health_assessment);
    let listener = UnixListener::bind(socket)?;
    fs::set_permissions(socket, fs::Permissions::from_mode(0o660))?;
    let _ = Command::new("/bin/chown")
        .args(["0:1000", SOCKET_PATH])
        .status();
    marker("SYNVEIL_AB_AGENT_READY");
    match health.join() {
        Ok(Ok("health-failed")) => {
            std::thread::sleep(Duration::from_secs(1));
            reboot_now();
        }
        Ok(Ok(outcome)) => marker(&format!("SYNVEIL_AB_HEALTH outcome={outcome}")),
        Ok(Err(error)) => marker(&format!("SYNVEIL_AB_HEALTH_ERROR error={error}")),
        Err(_) => marker("SYNVEIL_AB_HEALTH_ERROR error=health thread panicked"),
    }
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let _ = stream.set_read_timeout(Some(Duration::from_secs(120)));
        let mut reader = std::io::BufReader::new(match stream.try_clone() {
            Ok(clone) => clone,
            Err(_) => continue,
        });
        let mut line = Vec::new();
        while let Ok(Some(frame)) = veil_protocol::read_frame(&mut reader) {
            line.clone_from(&frame);
            if line.len() > MAX_REQUEST_BYTES {
                break;
            }
            let parsed: Value = match serde_json::from_slice(&line) {
                Ok(value) => value,
                Err(_) => break,
            };
            if parsed.get("schema").and_then(Value::as_str) != Some(AGENT_SCHEMA) {
                break;
            }
            let action = parsed.get("action").and_then(Value::as_str).unwrap_or("");
            let id = parsed.get("id").and_then(Value::as_str).unwrap_or("?");
            let mut response = dispatch(action);
            response["schema"] = Value::String(AGENT_SCHEMA.into());
            response["id"] = Value::String(id.to_string());
            let mut text = serde_json::to_string(&response).unwrap_or_default();
            text.push('\n');
            if stream.write_all(text.as_bytes()).is_err() {
                break;
            }
        }
    }
    Ok(())
}

pub fn request(action: &str) -> io::Result<Value> {
    let mut stream = UnixStream::connect(SOCKET_PATH)?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(300)));
    let request = json!({"schema": AGENT_SCHEMA, "id": "client", "action": action});
    let mut text = serde_json::to_string(&request).map_err(|_| err("encode failed"))?;
    text.push('\n');
    stream.write_all(text.as_bytes())?;
    let mut reader = std::io::BufReader::new(stream);
    let frame = veil_protocol::read_frame(&mut reader)?
        .ok_or_else(|| err("agent closed the connection without a reply"))?;
    let response: Value =
        serde_json::from_slice(&frame).map_err(|_| err("agent reply does not parse"))?;
    Ok(response)
}
