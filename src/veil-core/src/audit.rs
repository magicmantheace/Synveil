// SPDX-License-Identifier: MPL-2.0

use serde_json::{Value, json};
use std::ffi::{CString, c_char};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const AUDIT_SCHEMA_V1: &str = "synveil.audit/v1";
// Prototype protection against unbounded persistent journal growth. A
// production release still requires retention policy and independent integrity.
const MAX_AUDIT_BYTES: u64 = 4 * 1024 * 1024;
const MAX_AUDIT_RECORD_BYTES: usize = 64 * 1024;
const LINUX_LOCK_EX_NONBLOCK: i32 = 2 | 4;

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
    fn renameat2(
        old_dir: i32,
        old: *const c_char,
        new_dir: i32,
        new: *const c_char,
        flags: u32,
    ) -> i32;
}

static SEQUENCE: AtomicU64 = AtomicU64::new(1);
static PERSISTENT_WRITE_ATTEMPTED: AtomicBool = AtomicBool::new(false);
static LAST_PERSISTENT_WRITE_SUCCEEDED: AtomicBool = AtomicBool::new(false);
static PERSISTENT_WRITE_FAILURES: AtomicU64 = AtomicU64::new(0);

pub fn persistent_status() -> veil_protocol::PersistentAuditStatus {
    let configured = std::env::var_os("SYNVEIL_AUDIT_PATH").is_some();
    let state = if !configured {
        "disabled"
    } else if !PERSISTENT_WRITE_ATTEMPTED.load(Ordering::SeqCst) {
        "starting"
    } else if LAST_PERSISTENT_WRITE_SUCCEEDED.load(Ordering::SeqCst) {
        "healthy"
    } else {
        "degraded"
    };
    veil_protocol::PersistentAuditStatus {
        configured,
        state: state.to_owned(),
        failed_writes: PERSISTENT_WRITE_FAILURES.load(Ordering::SeqCst),
    }
}

pub fn record(event: &str, fields: Value) -> Value {
    let unix_time_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok());
    record_at(event, fields, unix_time_ms)
}

fn record_at(event: &str, fields: Value, unix_time_ms: Option<u64>) -> Value {
    json!({
        "schema": AUDIT_SCHEMA_V1,
        "component": "veil-core",
        "pid": std::process::id(),
        "sequence": SEQUENCE.fetch_add(1, Ordering::Relaxed),
        "unix_time_ms": unix_time_ms,
        "version": veil_protocol::BUILD_VERSION,
        "git_revision": veil_protocol::BUILD_REVISION,
        "event": event,
        "fields": fields,
    })
}

pub fn emit(event: &str, fields: Value) {
    let line = record(event, fields).to_string();
    eprintln!("{line}");
    // Opt-in audit journal, only set by bootstrap after /var is mounted.
    // Console logging remains available even if persistent storage fails.
    if let Some(path) = std::env::var_os("SYNVEIL_AUDIT_PATH") {
        match append_record(Path::new(&path), &line) {
            Ok(()) => {
                LAST_PERSISTENT_WRITE_SUCCEEDED.store(true, Ordering::SeqCst);
                PERSISTENT_WRITE_ATTEMPTED.store(true, Ordering::SeqCst);
            }
            Err(error) => {
                PERSISTENT_WRITE_FAILURES.fetch_add(1, Ordering::SeqCst);
                LAST_PERSISTENT_WRITE_SUCCEEDED.store(false, Ordering::SeqCst);
                PERSISTENT_WRITE_ATTEMPTED.store(true, Ordering::SeqCst);
                eprintln!("[synveil-audit] persistent write failed: {error}");
            }
        }
    }
}

// Linux O_NOFOLLOW and O_NONBLOCK: refuse final symlinks and FIFOs.
const LINUX_O_NOFOLLOW: i32 = 0o400000;
const LINUX_O_NONBLOCK: i32 = 0o4000;

fn unsafe_journal() -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, "unsafe audit journal path")
}

// All rotating writers and inspectors acquire this stable inode before opening
// a journal. A per-journal flock alone cannot serialize writers across rename.
pub(crate) fn directory_lock(path: &Path, write: bool) -> io::Result<Option<fs::File>> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let lock_path = parent.join(".audit-lock");
    if path.file_name() == lock_path.file_name() {
        return Err(unsafe_journal());
    }
    if !write {
        match fs::symlink_metadata(&lock_path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
            Ok(_) => {}
        }
    }
    let directory = fs::symlink_metadata(parent)?;
    if !directory.is_dir() || directory.mode() & 0o022 != 0 {
        return Err(unsafe_journal());
    }
    let lock = OpenOptions::new()
        .read(true)
        .write(write)
        .create(write)
        .truncate(false)
        .mode(0o600)
        .custom_flags(LINUX_O_NOFOLLOW | LINUX_O_NONBLOCK)
        .open(lock_path)?;
    let meta = lock.metadata()?;
    if !meta.is_file()
        || meta.uid() != directory.uid()
        || meta.nlink() != 1
        || meta.mode() & 0o777 != 0o600
        || meta.len() != 0
    {
        return Err(unsafe_journal());
    }
    let operation = if write { LINUX_LOCK_EX_NONBLOCK } else { 1 | 4 };
    if unsafe { flock(lock.as_raw_fd(), operation) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(Some(lock))
}

fn archive_path(path: &Path) -> io::Result<PathBuf> {
    let mut name = path.file_name().ok_or_else(unsafe_journal)?.to_os_string();
    name.push(".1");
    Ok(path.with_file_name(name))
}

fn preserve_archive(path: &Path) -> io::Result<()> {
    let old = CString::new(path.as_os_str().as_bytes()).map_err(|_| unsafe_journal())?;
    let archive = archive_path(path)?;
    let new = CString::new(archive.as_os_str().as_bytes()).map_err(|_| unsafe_journal())?;
    // Linux RENAME_NOREPLACE is atomic; files, symlinks and directories at the
    // archive destination are never replaced. Unsupported kernels fail closed.
    if unsafe { renameat2(-100, old.as_ptr(), -100, new.as_ptr(), 1) } != 0 {
        let error = io::Error::last_os_error();
        return Err(if error.kind() == io::ErrorKind::AlreadyExists {
            io::Error::new(
                error.kind(),
                "audit archive exists; retention capacity reached",
            )
        } else {
            error
        });
    }
    Ok(())
}

fn open_journal(path: &Path, directory: &fs::Metadata) -> io::Result<fs::File> {
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .append(true)
        .mode(0o600)
        .custom_flags(LINUX_O_NOFOLLOW | LINUX_O_NONBLOCK)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.is_file()
        || meta.uid() != directory.uid()
        || meta.nlink() != 1
        || meta.permissions().mode() & 0o777 != 0o600
    {
        return Err(unsafe_journal());
    }
    if unsafe { flock(file.as_raw_fd(), LINUX_LOCK_EX_NONBLOCK) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(file)
}

fn sync_directory(parent: &Path, directory: &fs::Metadata) -> io::Result<()> {
    let opened = OpenOptions::new()
        .read(true)
        .custom_flags(LINUX_O_NOFOLLOW | LINUX_O_NONBLOCK)
        .open(parent)?;
    let meta = opened.metadata()?;
    if !meta.is_dir() || meta.dev() != directory.dev() || meta.ino() != directory.ino() {
        return Err(unsafe_journal());
    }
    opened.sync_all()
}

// Keep storage operations statically dispatched so tests can inject I/O
// errors at each boundary without adding runtime environment-driven failpoints.
trait JournalIo {
    fn write(&mut self, file: &mut fs::File, bytes: &[u8]) -> io::Result<()> {
        file.write_all(bytes)
    }

    fn sync_file(&mut self, file: &fs::File) -> io::Result<()> {
        file.sync_all()
    }

    fn archive(&mut self, path: &Path) -> io::Result<()> {
        preserve_archive(path)
    }

    fn sync_parent(&mut self, parent: &Path, directory: &fs::Metadata) -> io::Result<()> {
        sync_directory(parent, directory)
    }
}

struct FilesystemIo;
impl JournalIo for FilesystemIo {}

fn append_record(path: &Path, line: &str) -> io::Result<()> {
    append_record_with_io(path, line, &mut FilesystemIo)
}

fn append_record_with_io(path: &Path, line: &str, storage: &mut impl JournalIo) -> io::Result<()> {
    // A record must remain one bounded line even if an internal caller errs.
    if line.is_empty() || line.contains(['\n', '\r']) || line.len() >= MAX_AUDIT_RECORD_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "audit event must be one bounded nonempty line",
        ));
    }
    let parent = path.parent().ok_or_else(unsafe_journal)?;
    let directory = fs::symlink_metadata(parent)?;
    if !directory.file_type().is_dir() || directory.mode() & 0o022 != 0 {
        return Err(unsafe_journal());
    }
    let _lock = directory_lock(path, true)?;
    // Refuse existing suspicious objects, then validate the opened inode.
    match fs::symlink_metadata(path) {
        Ok(meta) if !meta.file_type().is_file() => return Err(unsafe_journal()),
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let mut file = open_journal(path, &directory)?;
    let mut bytes = line.as_bytes().to_vec();
    bytes.push(b'\n');
    if bytes.len() as u64 > MAX_AUDIT_BYTES {
        return Err(io::Error::other("audit event exceeds journal limit"));
    }
    let current = file.metadata()?.len();
    if current > MAX_AUDIT_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "audit journal exceeds capacity",
        ));
    }
    if current != 0 {
        // A crash or failed write may leave an incomplete final record. Never
        // concatenate the next event onto it or silently discard evidence.
        file.seek(SeekFrom::End(-1))?;
        let mut last = [0];
        file.read_exact(&mut last)?;
        if last[0] != b'\n' {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "persistent audit journal has an incomplete final record",
            ));
        }
    }
    if current
        .checked_add(bytes.len() as u64)
        .is_none_or(|next| next > MAX_AUDIT_BYTES)
    {
        storage.sync_file(&file)?;
        storage.archive(path)?;
        // Commit the archive rename before creating the next active journal.
        storage.sync_parent(parent, &directory)?;
        eprintln!("[synveil-audit] full journal preserved in .1 archive");
        file = open_journal(path, &directory)?;
    }
    storage.write(&mut file, &bytes)?;
    storage.sync_file(&file)?;
    // File synchronization alone does not persist a newly created directory
    // entry. Repeat this after every append, including retries after failure.
    storage.sync_parent(parent, &directory)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_build_process_and_event_identity() {
        let value = record_at(
            "request_rejected",
            json!({ "code": "malformed_message" }),
            Some(123),
        );
        assert_eq!(value["schema"], AUDIT_SCHEMA_V1);
        assert_eq!(value["component"], "veil-core");
        assert_eq!(value["pid"], std::process::id());
        assert_eq!(value["version"], veil_protocol::BUILD_VERSION);
        assert_eq!(value["event"], "request_rejected");
        assert_eq!(value["fields"]["code"], "malformed_message");
        assert_eq!(value["unix_time_ms"], 123);
    }

    #[test]
    fn sequences_increase_within_process() {
        let first = record("start", json!({}))["sequence"].as_u64().unwrap();
        let second = record("shutdown", json!({}))["sequence"].as_u64().unwrap();
        assert!(first > 0 && second > first);
    }

    #[test]
    fn unavailable_clock_is_explicit() {
        assert!(record_at("start", json!({}), None)["unix_time_ms"].is_null());
    }

    fn test_dir(label: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "synveil-audit-{label}-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn symlinks_are_rejected_without_touching_target() {
        let dir = test_dir("link");
        let target = dir.join("target");
        fs::write(&target, b"original").unwrap();
        let journal = dir.join("journal");
        std::os::unix::fs::symlink(&target, &journal).unwrap();
        assert_eq!(
            append_record(&journal, "injected").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read(&target).unwrap(), b"original");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn hardlinks_and_permissive_journals_are_rejected() {
        let dir = test_dir("hardlink");
        let original = dir.join("original");
        fs::write(&original, b"unchanged").unwrap();
        fs::set_permissions(&original, fs::Permissions::from_mode(0o600)).unwrap();
        let journal = dir.join("journal");
        fs::hard_link(&original, &journal).unwrap();
        assert_eq!(
            append_record(&journal, "injected").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        fs::remove_file(&journal).unwrap();
        fs::set_permissions(&original, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            append_record(&original, "injected").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read(&original).unwrap(), b"unchanged");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn private_append_succeeds_but_unsafe_parent_refuses_write() {
        let dir = test_dir("parent");
        let journal = dir.join("journal");
        append_fixture(&journal, "first").unwrap();
        append_fixture(&journal, "second").unwrap();
        assert_eq!(fs::read_to_string(&journal).unwrap(), "first\nsecond\n");
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o777)).unwrap();
        assert_eq!(
            append_record(&journal, "third").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read_to_string(&journal).unwrap(), "first\nsecond\n");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn bounded_audit_journal_refuses_growth_before_disk_exhaustion() {
        let dir = test_dir("capacity");
        let journal = dir.join("journal");
        let initial = vec![b'x'; (MAX_AUDIT_BYTES - 3) as usize];
        fs::write(&journal, &initial).unwrap();
        // The capacity fixture represents a complete existing record.
        let mut file = OpenOptions::new().append(true).open(&journal).unwrap();
        file.write_all(b"\n").unwrap();
        fs::set_permissions(&journal, fs::Permissions::from_mode(0o600)).unwrap();
        append_fixture(&journal, "x").unwrap();
        assert_eq!(fs::metadata(&journal).unwrap().len(), MAX_AUDIT_BYTES);
        // Exhausted retention never overwrites an existing archive.
        fs::write(archive_path(&journal).unwrap(), b"retained archive").unwrap();
        assert!(append_record(&journal, "more").is_err());
        assert_eq!(fs::metadata(&journal).unwrap().len(), MAX_AUDIT_BYTES);
        assert_eq!(fs::read(&journal).unwrap().last(), Some(&b'\n'));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn incomplete_tail_is_preserved_and_refuses_future_appends() {
        let dir = test_dir("torn-tail");
        let journal = dir.join("journal");
        append_fixture(&journal, "complete").unwrap();
        let mut file = OpenOptions::new().append(true).open(&journal).unwrap();
        file.write_all(b"partial").unwrap();
        assert_eq!(
            append_record(&journal, "next").unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        assert_eq!(fs::read(&journal).unwrap(), b"complete\npartial");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn malformed_or_oversized_events_leave_journal_unchanged() {
        let dir = test_dir("invalid-event");
        let journal = dir.join("journal");
        append_fixture(&journal, "original").unwrap();
        for line in [
            "",
            "one\ntwo",
            "one\rtwo",
            &"x".repeat(MAX_AUDIT_RECORD_BYTES),
        ] {
            assert_eq!(
                append_record(&journal, line).unwrap_err().kind(),
                io::ErrorKind::InvalidInput
            );
        }
        assert_eq!(fs::read(&journal).unwrap(), b"original\n");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn writer_lock_contention_refuses_without_mutation_and_recovers() {
        let dir = test_dir("locked");
        let journal = dir.join("journal");
        // Seed without a transient append lock: another parallel test may fork
        // while that descriptor is open, inheriting its flock until exec.
        fs::write(&journal, b"original\n").unwrap();
        fs::set_permissions(&journal, fs::Permissions::from_mode(0o600)).unwrap();
        let held = OpenOptions::new().append(true).open(&journal).unwrap();
        assert_eq!(
            unsafe { flock(held.as_raw_fd(), LINUX_LOCK_EX_NONBLOCK) },
            0
        );
        assert_eq!(
            append_record(&journal, "blocked").unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert_eq!(fs::read(&journal).unwrap(), b"original\n");
        // Release the shared open-file-description lock even if a concurrent
        // fork briefly inherited this descriptor before its close-on-exec.
        assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        drop(held);
        append_fixture(&journal, "recovered").unwrap();
        assert_eq!(fs::read(&journal).unwrap(), b"original\nrecovered\n");
        fs::remove_dir_all(dir).unwrap();
    }

    fn seed_full_journal(path: &Path) -> Vec<u8> {
        let line = format!(
            "{}\n",
            record("padding", json!({"padding": "x".repeat(4096)}))
        );
        let bytes = line
            .repeat(MAX_AUDIT_BYTES as usize / line.len())
            .into_bytes();
        fs::write(path, &bytes).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
        bytes
    }

    #[test]
    fn rotation_preserves_archive_and_resumes_with_inspectable_records() {
        let dir = test_dir("rotation");
        let journal = dir.join("journal");
        let previous = seed_full_journal(&journal);
        let next = record("after_rotation", json!({"padding": "y".repeat(8192)})).to_string();
        append_fixture(&journal, &next).unwrap();
        let archive = archive_path(&journal).unwrap();
        assert_eq!(fs::read(&archive).unwrap(), previous);
        assert_eq!(fs::read_to_string(&journal).unwrap(), format!("{next}\n"));
        assert_eq!(fs::metadata(&archive).unwrap().mode() & 0o777, 0o600);
        inspect_fixture(&archive).unwrap();
        append_fixture(&journal, &record("restart", json!({})).to_string()).unwrap();
        assert_eq!(inspect_fixture(&journal).unwrap()["records"], 2);
        assert_eq!(fs::read(&archive).unwrap(), previous);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn archive_collision_and_incomplete_tail_never_erase_evidence() {
        let dir = test_dir("archive-collision");
        let journal = dir.join("journal");
        let previous = seed_full_journal(&journal);
        let archive = archive_path(&journal).unwrap();
        fs::write(&archive, b"existing history").unwrap();
        let next = record("next", json!({"padding": "y".repeat(8192)})).to_string();
        assert_eq!(
            append_record(&journal, &next).unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(fs::read(&journal).unwrap(), previous);
        assert_eq!(fs::read(&archive).unwrap(), b"existing history");
        fs::remove_file(&archive).unwrap();
        let mut torn = previous.clone();
        torn.pop();
        fs::write(&journal, &torn).unwrap();
        assert_eq!(
            append_record(&journal, &next).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        assert!(!archive.exists());
        assert_eq!(fs::read(&journal).unwrap(), torn);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn stable_directory_lock_blocks_readers_and_writers_without_mutation() {
        let dir = test_dir("stable-lock");
        let journal = dir.join("journal");
        let original = record("start", json!({})).to_string();
        append_fixture(&journal, &original).unwrap();
        let held = retry_fixture(|| directory_lock(&journal, true)).unwrap();
        assert_eq!(
            append_record(&journal, "blocked").unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert_eq!(
            super::super::audit_check::inspect(&journal)
                .unwrap_err()
                .kind(),
            io::ErrorKind::WouldBlock
        );
        assert_eq!(
            fs::read_to_string(&journal).unwrap(),
            format!("{original}\n")
        );
        drop(held);
        inspect_fixture(&journal).unwrap();
        let reader = retry_fixture(|| directory_lock(&journal, false)).unwrap();
        assert_eq!(
            append_record(&journal, "blocked").unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        drop(reader);
        let lock_path = dir.join(".audit-lock");
        fs::write(&lock_path, b"unsafe lock content").unwrap();
        assert_eq!(
            append_record(&journal, "blocked").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(
            fs::read_to_string(&journal).unwrap(),
            format!("{original}\n")
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn restart_after_archive_rename_creates_new_journal_without_touching_archive() {
        let dir = test_dir("rename-restart");
        let journal = dir.join("journal");
        let previous = seed_full_journal(&journal);
        preserve_archive(&journal).unwrap();
        let next = record("restart", json!({})).to_string();
        append_fixture(&journal, &next).unwrap();
        assert_eq!(fs::read(archive_path(&journal).unwrap()).unwrap(), previous);
        assert_eq!(fs::read_to_string(&journal).unwrap(), format!("{next}\n"));
        fs::remove_dir_all(dir).unwrap();
    }

    struct FaultingIo {
        fail_at: usize,
        calls: Vec<&'static str>,
        partial_write: bool,
    }

    impl FaultingIo {
        fn new(fail_at: usize) -> Self {
            Self {
                fail_at,
                calls: Vec::new(),
                partial_write: false,
            }
        }

        fn step(&mut self, operation: &'static str) -> io::Result<()> {
            self.calls.push(operation);
            if self.calls.len() == self.fail_at {
                return Err(io::Error::from_raw_os_error(5)); // Linux EIO
            }
            Ok(())
        }
    }

    impl JournalIo for FaultingIo {
        fn write(&mut self, file: &mut fs::File, bytes: &[u8]) -> io::Result<()> {
            if let Err(error) = self.step("write") {
                if self.partial_write {
                    file.write_all(&bytes[..17])?;
                }
                return Err(error);
            }
            FilesystemIo.write(file, bytes)
        }

        fn sync_file(&mut self, file: &fs::File) -> io::Result<()> {
            self.step("sync_file")?;
            FilesystemIo.sync_file(file)
        }

        fn archive(&mut self, path: &Path) -> io::Result<()> {
            self.step("archive")?;
            FilesystemIo.archive(path)
        }

        fn sync_parent(&mut self, parent: &Path, directory: &fs::Metadata) -> io::Result<()> {
            self.step("sync_parent")?;
            FilesystemIo.sync_parent(parent, directory)
        }
    }

    // Forked test processes can inherit an open flock until exec. Retry only
    // that pre-write contention for fixture operations whose intended result
    // is not lock contention.
    // Production callers and tests asserting contention still fail immediately.
    fn append_fixture(path: &Path, line: &str) -> io::Result<()> {
        retry_fixture(|| append_record(path, line))
    }

    fn retry_fixture<T>(mut operation: impl FnMut() -> io::Result<T>) -> io::Result<T> {
        for _ in 0..100 {
            match operation() {
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                result => return result,
            }
        }
        operation()
    }

    fn inspect_fixture(path: &Path) -> io::Result<Value> {
        // Parallel seat tests fork while audit descriptors can be open. A
        // child can briefly inherit their flock until exec closes the fd.
        // Wait only for WouldBlock; malformed data and all other errors fail
        // immediately. Production inspection remains strictly nonblocking.
        for _ in 0..100 {
            match super::super::audit_check::inspect(path) {
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    eprintln!("[audit-fixture] waiting for inherited lock: {error}");
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                result => return result,
            }
        }
        super::super::audit_check::inspect(path)
    }

    #[test]
    fn fixture_append_waits_for_contention_without_repeating_records() {
        let dir = test_dir("fixture-append");
        let journal = dir.join("journal");
        let first = record("original", json!({})).to_string();
        append_fixture(&journal, &first).unwrap();
        let held = directory_lock(&journal, true).unwrap().unwrap();
        let next = record("after_lock", json!({})).to_string();
        assert_eq!(
            append_record(&journal, &next).unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        let release = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(30));
            assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        });
        append_fixture(&journal, &next).unwrap();
        release.join().unwrap();
        assert_eq!(
            fs::read_to_string(&journal).unwrap(),
            format!("{first}\n{next}\n")
        );
        assert_eq!(inspect_fixture(&journal).unwrap()["records"], 2);
        fs::write(&journal, b"partial").unwrap();
        assert_eq!(
            append_fixture(&journal, &next).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        assert_eq!(fs::read(&journal).unwrap(), b"partial");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn fixture_inspection_waits_for_contention_but_rejects_invalid_data() {
        let dir = test_dir("fixture-inspection");
        let journal = dir.join("journal");
        append_fixture(&journal, &record("original", json!({})).to_string()).unwrap();
        let held = directory_lock(&journal, true).unwrap().unwrap();
        assert_eq!(
            super::super::audit_check::inspect(&journal)
                .unwrap_err()
                .kind(),
            io::ErrorKind::WouldBlock
        );
        let release = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(30));
            assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        });
        assert_eq!(inspect_fixture(&journal).unwrap()["records"], 1);
        release.join().unwrap();
        fs::write(&journal, b"partial").unwrap();
        assert_eq!(
            inspect_fixture(&journal).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn append_io_errors_preserve_history_report_failure_and_allow_retry() {
        let operations = ["write", "sync_file", "sync_parent"];
        for fail_at in 1..=operations.len() {
            let dir = test_dir("append-io");
            let journal = dir.join("journal");
            let original = record("original", json!({})).to_string();
            append_fixture(&journal, &original).unwrap();
            let next = record("unacknowledged", json!({})).to_string();
            let mut storage = FaultingIo::new(fail_at);
            let error = append_record_with_io(&journal, &next, &mut storage).unwrap_err();
            assert_eq!(error.raw_os_error(), Some(5));
            assert_eq!(storage.calls, operations[..fail_at]);
            // A failed sync can leave a complete but unacknowledged record.
            let expected = if fail_at == 1 {
                format!("{original}\n")
            } else {
                format!("{original}\n{next}\n")
            };
            assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
            assert!(!archive_path(&journal).unwrap().exists());
            let retry = record("retry", json!({})).to_string();
            append_fixture(&journal, &retry).unwrap();
            assert_eq!(
                fs::read_to_string(&journal).unwrap(),
                format!("{expected}{retry}\n")
            );
            inspect_fixture(&journal).unwrap();
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn rotation_io_errors_preserve_all_history_and_recover_without_overwrite() {
        let operations = [
            "sync_file",
            "archive",
            "sync_parent",
            "write",
            "sync_file",
            "sync_parent",
        ];
        for fail_at in 1..=operations.len() {
            let dir = test_dir("rotation-io");
            let journal = dir.join("journal");
            let previous = seed_full_journal(&journal);
            let original_inode = fs::metadata(&journal).unwrap().ino();
            let archive = archive_path(&journal).unwrap();
            let next = record("next", json!({"padding": "y".repeat(8192)})).to_string();
            let mut storage = FaultingIo::new(fail_at);
            let error = append_record_with_io(&journal, &next, &mut storage).unwrap_err();
            assert_eq!(error.raw_os_error(), Some(5));
            assert_eq!(storage.calls, operations[..fail_at]);
            if fail_at <= 2 {
                assert_eq!(fs::read(&journal).unwrap(), previous);
                assert!(!archive.exists());
            } else {
                assert_eq!(fs::read(&archive).unwrap(), previous);
                if fail_at == 3 {
                    assert!(!journal.exists());
                } else {
                    let expected = if fail_at == 4 {
                        String::new()
                    } else {
                        format!("{next}\n")
                    };
                    assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
                }
            }
            // Use a new event: a returned error does not imply zero bytes wrote
            // and does not grant exactly-once semantics to an automatic retry.
            let retry = record("retry", json!({"padding": "z".repeat(8192)})).to_string();
            append_fixture(&journal, &retry).unwrap();
            assert_eq!(fs::read(&archive).unwrap(), previous);
            assert_eq!(fs::metadata(&archive).unwrap().ino(), original_inode);
            let expected = if fail_at >= 5 {
                format!("{next}\n{retry}\n")
            } else {
                format!("{retry}\n")
            };
            assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
            inspect_fixture(&archive).unwrap();
            inspect_fixture(&journal).unwrap();
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn partial_write_error_preserves_tail_and_refuses_retry_without_touching_archive() {
        let dir = test_dir("partial-write-io");
        let journal = dir.join("journal");
        let original = record("original", json!({})).to_string();
        append_fixture(&journal, &original).unwrap();
        let archive = archive_path(&journal).unwrap();
        fs::write(&archive, b"retained history\n").unwrap();
        fs::set_permissions(&archive, fs::Permissions::from_mode(0o600)).unwrap();
        let next = record("partial", json!({})).to_string();
        let mut storage = FaultingIo::new(1);
        storage.partial_write = true;
        assert_eq!(
            retry_fixture(|| append_record_with_io(&journal, &next, &mut storage))
                .unwrap_err()
                .raw_os_error(),
            Some(5)
        );
        let expected = format!("{original}\n{}", &next[..17]);
        assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
        assert_eq!(
            retry_fixture(|| append_record(&journal, "retry"))
                .unwrap_err()
                .kind(),
            io::ErrorKind::InvalidData
        );
        assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
        assert_eq!(fs::read(&archive).unwrap(), b"retained history\n");
        assert_eq!(
            inspect_fixture(&journal).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn partial_write_after_rotation_preserves_archive_and_refuses_incomplete_active() {
        let dir = test_dir("rotation-partial-io");
        let journal = dir.join("journal");
        let previous = seed_full_journal(&journal);
        let archive = archive_path(&journal).unwrap();
        let next = record("partial", json!({"padding": "y".repeat(8192)})).to_string();
        let mut storage = FaultingIo::new(4);
        storage.partial_write = true;
        assert_eq!(
            append_record_with_io(&journal, &next, &mut storage)
                .unwrap_err()
                .raw_os_error(),
            Some(5)
        );
        assert_eq!(fs::read(&archive).unwrap(), previous);
        assert_eq!(fs::read(&journal).unwrap(), next.as_bytes()[..17]);
        assert_eq!(
            append_record(&journal, "retry").unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        assert_eq!(fs::read(&archive).unwrap(), previous);
        assert_eq!(fs::read(&journal).unwrap(), next.as_bytes()[..17]);
        inspect_fixture(&archive).unwrap();
        assert_eq!(
            inspect_fixture(&journal).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        fs::remove_dir_all(dir).unwrap();
    }

    struct ReplacedActiveIo {
        journal: PathBuf,
        target: PathBuf,
        kind: &'static str,
        installed: bool,
        installed_inode: Option<u64>,
        writes: usize,
    }

    impl JournalIo for ReplacedActiveIo {
        fn sync_parent(&mut self, parent: &Path, directory: &fs::Metadata) -> io::Result<()> {
            FilesystemIo.sync_parent(parent, directory)?;
            assert!(!self.installed);
            assert!(archive_path(&self.journal)?.exists());
            assert!(!self.journal.exists());
            match self.kind {
                "symlink" => std::os::unix::fs::symlink(&self.target, &self.journal)?,
                "hardlink" => fs::hard_link(&self.target, &self.journal)?,
                "directory" => fs::create_dir(&self.journal)?,
                "fifo" => {
                    unsafe extern "C" {
                        fn mkfifo(path: *const c_char, mode: u32) -> i32;
                    }
                    let path = CString::new(self.journal.as_os_str().as_bytes()).unwrap();
                    if unsafe { mkfifo(path.as_ptr(), 0o600) } != 0 {
                        return Err(io::Error::last_os_error());
                    }
                }
                _ => unreachable!(),
            }
            self.installed_inode = Some(fs::symlink_metadata(&self.journal)?.ino());
            self.installed = true;
            Ok(())
        }

        fn write(&mut self, file: &mut fs::File, bytes: &[u8]) -> io::Result<()> {
            self.writes += 1;
            FilesystemIo.write(file, bytes)
        }
    }

    #[test]
    fn unsafe_active_replacement_after_rotation_preserves_archive_and_other_file() {
        for kind in ["symlink", "hardlink", "directory", "fifo"] {
            let dir = test_dir("rotation-replacement");
            let journal = dir.join("journal");
            let previous = seed_full_journal(&journal);
            let original_inode = fs::metadata(&journal).unwrap().ino();
            let archive = archive_path(&journal).unwrap();
            let target = dir.join("unrelated");
            fs::write(&target, b"unrelated private data\n").unwrap();
            fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
            let target_inode = fs::metadata(&target).unwrap().ino();
            let mut storage = ReplacedActiveIo {
                journal: journal.clone(),
                target: target.clone(),
                kind,
                installed: false,
                installed_inode: None,
                writes: 0,
            };
            let next = record("next", json!({"padding": "y".repeat(8192)})).to_string();
            assert!(append_record_with_io(&journal, &next, &mut storage).is_err());
            assert!(
                storage.installed,
                "replacement boundary was not reached: {kind}"
            );
            assert_eq!(storage.writes, 0, "attempted write through {kind}");
            let refused = fs::symlink_metadata(&journal).unwrap();
            assert_eq!(refused.ino(), storage.installed_inode.unwrap());
            match kind {
                "symlink" => {
                    assert!(refused.file_type().is_symlink());
                    assert_eq!(fs::read_link(&journal).unwrap(), target);
                }
                "hardlink" => {
                    assert!(refused.is_file());
                    assert_eq!(refused.ino(), target_inode);
                    assert_eq!(refused.nlink(), 2);
                }
                "directory" => assert!(refused.is_dir()),
                "fifo" => {
                    use std::os::unix::fs::FileTypeExt;
                    assert!(refused.file_type().is_fifo());
                }
                _ => unreachable!(),
            }
            assert_eq!(fs::read(&target).unwrap(), b"unrelated private data\n");
            assert_eq!(fs::metadata(&target).unwrap().ino(), target_inode);
            assert_eq!(fs::read(&archive).unwrap(), previous);
            assert_eq!(fs::metadata(&archive).unwrap().ino(), original_inode);
            inspect_fixture(&archive).unwrap();
            // Only the fixture operator removes its injected object. The
            // audit writer itself must never unlink or repair that path.
            if kind == "directory" {
                fs::remove_dir(&journal).unwrap();
            } else {
                fs::remove_file(&journal).unwrap();
            }
            append_fixture(&journal, &next).unwrap();
            assert_eq!(fs::read_to_string(&journal).unwrap(), format!("{next}\n"));
            assert_eq!(fs::read(&archive).unwrap(), previous);
            assert_eq!(fs::metadata(&archive).unwrap().ino(), original_inode);
            assert_eq!(fs::read(&target).unwrap(), b"unrelated private data\n");
            assert_eq!(fs::metadata(&target).unwrap().nlink(), 1);
            inspect_fixture(&journal).unwrap();
            fs::remove_dir_all(dir).unwrap();
        }
    }

    fn kill_crash_worker() -> ! {
        unsafe extern "C" {
            fn raise(signal: i32) -> i32;
        }
        // SIGKILL runs no Rust destructors or graceful audit shutdown.
        unsafe { raise(9) };
        panic!("crash worker survived SIGKILL");
    }

    const CRASH_PHASES: [&str; 7] = [
        "renamed",
        "directory-synced",
        "empty-active",
        "partial-active",
        "written",
        "file-synced",
        "append-synced",
    ];

    struct ProcessCrashIo {
        phase: String,
        parent_syncs: usize,
        wrote: bool,
    }

    impl JournalIo for ProcessCrashIo {
        fn archive(&mut self, path: &Path) -> io::Result<()> {
            FilesystemIo.archive(path)?;
            if self.phase == "renamed" {
                kill_crash_worker();
            }
            Ok(())
        }

        fn sync_parent(&mut self, parent: &Path, directory: &fs::Metadata) -> io::Result<()> {
            FilesystemIo.sync_parent(parent, directory)?;
            self.parent_syncs += 1;
            if self.parent_syncs == 1 && self.phase == "directory-synced" {
                kill_crash_worker();
            }
            if self.wrote && self.phase == "append-synced" {
                kill_crash_worker();
            }
            Ok(())
        }

        fn sync_file(&mut self, file: &fs::File) -> io::Result<()> {
            FilesystemIo.sync_file(file)?;
            if self.wrote && self.phase == "file-synced" {
                kill_crash_worker();
            }
            Ok(())
        }

        fn write(&mut self, file: &mut fs::File, bytes: &[u8]) -> io::Result<()> {
            if self.phase == "empty-active" {
                kill_crash_worker();
            }
            if self.phase == "partial-active" {
                file.write_all(&bytes[..17])?;
                kill_crash_worker();
            }
            FilesystemIo.write(file, bytes)?;
            self.wrote = true;
            if self.phase == "written" {
                kill_crash_worker();
            }
            Ok(())
        }
    }

    #[test]
    fn process_crash_worker() {
        // Entry point only in the test executable; no production fault flags.
        let Some(root) = std::env::var_os("SYNVEIL_TEST_CRASH_ROOT") else {
            return;
        };
        let root = PathBuf::from(root);
        assert_eq!(root.parent().unwrap(), std::env::temp_dir());
        assert!(
            root.file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with("synveil-audit-process-crash-")
        );
        let metadata = fs::symlink_metadata(&root).unwrap();
        assert!(metadata.is_dir());
        assert_eq!(metadata.mode() & 0o777, 0o700);
        let phase = std::env::var("SYNVEIL_TEST_CRASH_PHASE").unwrap();
        assert!(CRASH_PHASES.contains(&phase.as_str()));
        let next = std::env::var("SYNVEIL_TEST_CRASH_RECORD").unwrap();
        let mut storage = ProcessCrashIo {
            phase,
            parent_syncs: 0,
            wrote: false,
        };
        append_record_with_io(&root.join("journal"), &next, &mut storage).unwrap();
        panic!("crash boundary was not reached");
    }

    fn run_crash_worker(dir: &Path, phase: &str, next: &str) {
        use std::os::unix::process::ExitStatusExt;
        use std::process::{Command, Stdio};
        use std::time::{Duration, Instant};

        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "audit::tests::process_crash_worker",
                "--test-threads=1",
            ])
            .env("SYNVEIL_TEST_CRASH_ROOT", dir)
            .env("SYNVEIL_TEST_CRASH_PHASE", phase)
            .env("SYNVEIL_TEST_CRASH_RECORD", next)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        let status = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break status;
            }
            if Instant::now() >= deadline {
                child.kill().unwrap();
                child.wait().unwrap();
                panic!("crash worker timed out at {phase}");
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        let mut diagnostic = String::new();
        child
            .stderr
            .take()
            .unwrap()
            .read_to_string(&mut diagnostic)
            .unwrap();
        assert_eq!(status.signal(), Some(9), "{phase}: {diagnostic}");
    }

    #[test]
    fn process_loss_during_rotation_preserves_history_and_releases_locks() {
        for phase in CRASH_PHASES {
            let dir = test_dir("process-crash");
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
            let journal = dir.join("journal");
            let previous = seed_full_journal(&journal);
            let original_inode = fs::metadata(&journal).unwrap().ino();
            // Establish a synchronized seed before the child starts. No sync
            // or cleanup follows the child's interrupted operations.
            fs::File::open(&journal).unwrap().sync_all().unwrap();
            sync_directory(&dir, &fs::metadata(&dir).unwrap()).unwrap();
            let next = record("interrupted", json!({"padding": "y".repeat(8192)})).to_string();
            run_crash_worker(&dir, phase, &next);
            let archive = archive_path(&journal).unwrap();
            assert_eq!(fs::read(&archive).unwrap(), previous);
            assert_eq!(fs::metadata(&archive).unwrap().ino(), original_inode);
            // A killed child must not leave either advisory lock held.
            inspect_fixture(&archive).unwrap();
            if phase == "renamed" || phase == "directory-synced" {
                assert!(!journal.exists());
            } else if phase == "empty-active" {
                assert!(fs::read(&journal).unwrap().is_empty());
            } else if phase == "partial-active" {
                assert_eq!(fs::read(&journal).unwrap(), next.as_bytes()[..17]);
                assert_eq!(
                    inspect_fixture(&journal).unwrap_err().kind(),
                    io::ErrorKind::InvalidData
                );
            } else {
                assert_eq!(fs::read_to_string(&journal).unwrap(), format!("{next}\n"));
                assert_eq!(inspect_fixture(&journal).unwrap()["records"], 1);
            }
            let retry = record("restart", json!({})).to_string();
            if phase == "partial-active" {
                assert_eq!(
                    append_record(&journal, &retry).unwrap_err().kind(),
                    io::ErrorKind::InvalidData
                );
                assert_eq!(fs::read(&journal).unwrap(), next.as_bytes()[..17]);
            } else {
                append_fixture(&journal, &retry).unwrap();
                let expected = if ["written", "file-synced", "append-synced"].contains(&phase) {
                    format!("{next}\n{retry}\n")
                } else {
                    format!("{retry}\n")
                };
                assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
                inspect_fixture(&journal).unwrap();
            }
            assert_eq!(fs::read(&archive).unwrap(), previous);
            assert_eq!(fs::metadata(&archive).unwrap().ino(), original_inode);
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn process_loss_during_first_or_existing_append_preserves_unacknowledged_bytes() {
        for existing in [false, true] {
            for phase in &CRASH_PHASES[2..] {
                let dir = test_dir("process-crash");
                fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
                let journal = dir.join("journal");
                let original = record("original", json!({})).to_string();
                let previous = if existing {
                    append_fixture(&journal, &original).unwrap();
                    format!("{original}\n")
                } else {
                    String::new()
                };
                let original_inode = fs::metadata(&journal).ok().map(|meta| meta.ino());
                sync_directory(&dir, &fs::metadata(&dir).unwrap()).unwrap();
                let next = record("unacknowledged", json!({})).to_string();
                run_crash_worker(&dir, phase, &next);
                let suffix = match *phase {
                    "empty-active" => String::new(),
                    "partial-active" => next[..17].to_string(),
                    _ => format!("{next}\n"),
                };
                let expected = format!("{previous}{suffix}");
                assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
                assert!(!archive_path(&journal).unwrap().exists());
                let metadata = fs::metadata(&journal).unwrap();
                assert_eq!(metadata.mode() & 0o777, 0o600);
                assert_eq!(metadata.nlink(), 1);
                if let Some(inode) = original_inode {
                    assert_eq!(metadata.ino(), inode);
                }
                let retry = record("restart", json!({})).to_string();
                if *phase == "partial-active" {
                    assert_eq!(
                        inspect_fixture(&journal).unwrap_err().kind(),
                        io::ErrorKind::InvalidData
                    );
                    assert_eq!(
                        append_record(&journal, &retry).unwrap_err().kind(),
                        io::ErrorKind::InvalidData
                    );
                    assert_eq!(fs::read_to_string(&journal).unwrap(), expected);
                } else {
                    // A distinct restart event preserves a complete record
                    // even though its caller never received acknowledgement.
                    append_fixture(&journal, &retry).unwrap();
                    assert_eq!(
                        fs::read_to_string(&journal).unwrap(),
                        format!("{expected}{retry}\n")
                    );
                    let records = usize::from(existing) + usize::from(!suffix.is_empty()) + 1;
                    assert_eq!(inspect_fixture(&journal).unwrap()["records"], records);
                }
                assert_eq!(fs::metadata(&journal).unwrap().ino(), metadata.ino());
                assert!(!archive_path(&journal).unwrap().exists());
                fs::remove_dir_all(dir).unwrap();
            }
        }
    }

    #[test]
    fn verified_archive_release_allows_next_rotation_without_losing_copy() {
        let dir = test_dir("retention-recovery");
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        let journal = dir.join("journal");
        let old = seed_full_journal(&journal);
        let next = record("after_rotation", json!({"padding": "y".repeat(8192)})).to_string();
        append_fixture(&journal, &next).unwrap();
        let copies = dir.join("copies");
        fs::create_dir(&copies).unwrap();
        fs::set_permissions(&copies, fs::Permissions::from_mode(0o700)).unwrap();
        let archive = archive_path(&journal).unwrap();
        let snapshot = copies.join("snapshot");
        let exported =
            retry_fixture(|| super::super::audit_export::export(&archive, &snapshot)).unwrap();
        retry_fixture(|| {
            super::super::audit_retention::release_archive(
                &journal,
                &snapshot,
                &copies.join("release.json"),
                exported["digest"].as_str().unwrap(),
            )
        })
        .unwrap();
        assert!(!archive.exists());
        assert_eq!(fs::read(&snapshot).unwrap(), old);
        let newer = seed_full_journal(&journal);
        append_fixture(&journal, &next).unwrap();
        assert_eq!(fs::read(&archive).unwrap(), newer);
        assert_eq!(fs::read(&snapshot).unwrap(), old);
        assert_eq!(fs::read_to_string(&journal).unwrap(), format!("{next}\n"));
        inspect_fixture(&journal).unwrap();
        inspect_fixture(&archive).unwrap();
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn unsafe_lock_objects_cannot_modify_journal_or_link_target() {
        let dir = test_dir("unsafe-lock");
        let journal = dir.join("journal");
        append_fixture(&journal, "original").unwrap();
        let lock = dir.join(".audit-lock");
        fs::remove_file(&lock).unwrap();
        let target = dir.join("target");
        fs::write(&target, b"private target").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
        std::os::unix::fs::symlink(&target, &lock).unwrap();
        assert!(append_record(&journal, "blocked").is_err());
        fs::remove_file(&lock).unwrap();
        fs::hard_link(&target, &lock).unwrap();
        assert!(append_record(&journal, "blocked").is_err());
        fs::remove_file(&lock).unwrap();
        fs::write(&lock, b"").unwrap();
        fs::set_permissions(&lock, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(append_record(&journal, "blocked").is_err());
        fs::remove_file(&lock).unwrap();
        assert!(
            std::process::Command::new("mkfifo")
                .arg(&lock)
                .status()
                .unwrap()
                .success()
        );
        assert!(append_record(&journal, "blocked").is_err());
        assert_eq!(fs::read(&journal).unwrap(), b"original\n");
        assert_eq!(fs::read(&target).unwrap(), b"private target");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn untrusted_fields_cannot_inject_record_lines() {
        let value = record("request", json!({ "id": "one\ntwo\r\nthree" }));
        let encoded = serde_json::to_string(&value).unwrap();
        assert!(!encoded.contains('\n') && !encoded.contains('\r'));
        assert_eq!(serde_json::from_str::<Value>(&encoded).unwrap(), value);
    }
}
