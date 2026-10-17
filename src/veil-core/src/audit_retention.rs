// SPDX-License-Identifier: MPL-2.0
//! Explicit offline archive release after durable copy and intent publication.
use serde_json::{Value, json};
use std::ffi::{CString, c_char};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::{Component, Path};

const NOFOLLOW_NONBLOCK: i32 = 0o400000 | 0o4000;
const MAX_BYTES: u64 = 4 * 1024 * 1024;

unsafe extern "C" {
    fn geteuid() -> u32;
    fn flock(fd: i32, operation: i32) -> i32;
    fn unlinkat(directory: i32, name: *const c_char, flags: i32) -> i32;
}

fn refused() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "unsafe audit retention input",
    )
}

fn absolute_normal(path: &Path) -> bool {
    path.is_absolute()
        && path.file_name().is_some()
        && path
            .components()
            .all(|part| matches!(part, Component::RootDir | Component::Normal(_)))
}

fn identity(meta: &fs::Metadata) -> (u64, u64) {
    (meta.dev(), meta.ino())
}

fn private_directory(path: &Path) -> io::Result<fs::File> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW_NONBLOCK)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.is_dir() || meta.uid() != unsafe { geteuid() } || meta.mode() & 0o777 != 0o700 {
        return Err(refused());
    }
    same_directory(path, &meta)?;
    Ok(file)
}

fn same_directory(path: &Path, expected: &fs::Metadata) -> io::Result<()> {
    let now = fs::symlink_metadata(path)?;
    if !now.is_dir()
        || identity(&now) != identity(expected)
        || now.uid() != unsafe { geteuid() }
        || now.mode() & 0o777 != 0o700
    {
        return Err(refused());
    }
    Ok(())
}

fn same_private_file(path: &Path, expected: &fs::Metadata) -> io::Result<()> {
    let now = fs::symlink_metadata(path)?;
    if !now.is_file()
        || identity(&now) != identity(expected)
        || now.len() != expected.len()
        || now.uid() != unsafe { geteuid() }
        || now.nlink() != 1
        || now.mode() & 0o777 != 0o600
    {
        return Err(refused());
    }
    Ok(())
}

// Private static dispatch permits deterministic storage-error tests without
// production fault flags or a runtime path that bypasses validation.
trait RetentionIo {
    fn sync_file(&mut self, file: &fs::File) -> io::Result<()> {
        file.sync_all()
    }

    fn sync_directory(&mut self, directory: &fs::File) -> io::Result<()> {
        directory.sync_all()
    }

    fn create_receipt(&mut self, directory: &fs::File) -> io::Result<fs::File> {
        super::audit_export::create_output(directory)
    }

    fn write_receipt(&mut self, file: &mut fs::File, record: &Value) -> io::Result<()> {
        writeln!(file, "{record}")
    }

    fn publish_receipt(
        &mut self,
        file: &fs::File,
        directory: &fs::File,
        name: &CString,
    ) -> io::Result<()> {
        super::audit_export::publish_output(file, directory, name)
    }

    fn unlink_archive(&mut self, directory: &fs::File, name: &CString) -> io::Result<()> {
        if unsafe { unlinkat(directory.as_raw_fd(), name.as_ptr(), 0) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

struct FilesystemRetentionIo;
impl RetentionIo for FilesystemRetentionIo {}

/// Remove only `<active>.1`, preserving a separately verified durable snapshot.
/// The caller must explicitly retain the supplied digest and authorization
/// receipt. No automatic retention or planner/IPC interface invokes this.
/// A post-unlink sync error may leave the archive absent: the durable receipt
/// records authorization, never a false claim that release completed.
pub fn release_archive(
    active: &Path,
    snapshot: &Path,
    authorization: &Path,
    expected: &str,
) -> io::Result<Value> {
    release_with_io(
        active,
        snapshot,
        authorization,
        expected,
        &mut FilesystemRetentionIo,
    )
}

fn release_with_io(
    active: &Path,
    snapshot: &Path,
    authorization: &Path,
    expected: &str,
    storage: &mut impl RetentionIo,
) -> io::Result<Value> {
    if ![active, snapshot, authorization]
        .into_iter()
        .all(absolute_normal)
        || active.file_name().unwrap() == ".audit-lock"
        || active
            .file_name()
            .unwrap()
            .as_encoded_bytes()
            .ends_with(b".1")
        || snapshot == authorization
        || authorization.file_name().unwrap() == ".audit-lock"
        || snapshot.parent() != authorization.parent()
        || expected.len() != 128
        || !expected
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid release arguments",
        ));
    }
    let parent = active.parent().ok_or_else(refused)?;
    let copies_parent = snapshot.parent().ok_or_else(refused)?;
    let directory = private_directory(parent)?;
    let copies_directory = private_directory(copies_parent)?;
    let directory_meta = directory.metadata()?;
    let copies_meta = copies_directory.metadata()?;
    if identity(&directory_meta) == identity(&copies_meta) {
        return Err(refused());
    }
    // Lock before opening either active/archive, serializing all cooperating
    // writers, rotation and inspection. Locks remain strictly nonblocking.
    let _lock = super::audit::directory_lock(active, true)?;
    let active_file = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW_NONBLOCK)
        .open(active)?;
    let active_meta = active_file.metadata()?;
    same_private_file(active, &active_meta)?;
    if unsafe { flock(active_file.as_raw_fd(), 2 | 4) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut current = Vec::new();
    (&active_file)
        .take(MAX_BYTES + 1)
        .read_to_end(&mut current)?;
    super::audit_check::inspect_bytes(&current)?;
    let mut name = active.file_name().unwrap().to_os_string();
    name.push(".1");
    let archive = active.with_file_name(name);
    let archive_file = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW_NONBLOCK)
        .open(&archive)?;
    let archive_meta = archive_file.metadata()?;
    same_private_file(&archive, &archive_meta)?;
    if identity(&active_meta) == identity(&archive_meta) {
        return Err(refused());
    }
    if unsafe { flock(archive_file.as_raw_fd(), 2 | 4) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut archived = Vec::new();
    (&archive_file)
        .take(MAX_BYTES + 1)
        .read_to_end(&mut archived)?;
    let inspected = super::audit_check::inspect_bytes(&archived)?;
    if archived.is_empty() || super::audit_export::checksum(&archived) != expected {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "archive digest mismatch",
        ));
    }
    super::audit_check::with_snapshot_file(snapshot, |copy, bytes, _| {
        let copy_meta = copy.metadata()?;
        same_private_file(snapshot, &copy_meta)?;
        if bytes != archived.as_slice()
            || identity(&copy_meta) == identity(&archive_meta)
            || identity(&copy_meta) == identity(&active_meta)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "snapshot mismatch",
            ));
        }
        // Re-establish copy durability before publishing any release intent.
        storage.sync_file(copy)?;
        storage.sync_directory(&copies_directory)?;
        let intent = json!({
            "schema": "synveil.audit-retention-authorization/v1",
            "operation": "release-archive",
            "phase": "authorized-before-unlink",
            "uid": unsafe { geteuid() },
            "algorithm": "blake2b-512",
            "digest": expected,
            "bytes": archived.len(),
            "records": inspected["records"],
            "active_device": active_meta.dev(),
            "active_inode": active_meta.ino(),
            "archive_device": archive_meta.dev(),
            "archive_inode": archive_meta.ino(),
            "snapshot_device": copy_meta.dev(),
            "snapshot_inode": copy_meta.ino(),
            "authenticated": false,
        });
        let mut receipt = storage.create_receipt(&copies_directory)?;
        storage.write_receipt(&mut receipt, &intent)?;
        storage.sync_file(&receipt)?;
        same_directory(copies_parent, &copies_meta)?;
        storage.publish_receipt(
            &receipt,
            &copies_directory,
            &super::audit_export::output_name(authorization)?,
        )?;
        storage.sync_directory(&copies_directory)?;
        // A collision or validation error leaves every journal intact. An
        // intent may remain after failure; no cleanup unlinks by pathname.
        same_private_file(authorization, &receipt.metadata()?)?;
        same_private_file(snapshot, &copy_meta)?;
        same_private_file(active, &active_meta)?;
        same_private_file(&archive, &archive_meta)?;
        same_directory(copies_parent, &copies_meta)?;
        same_directory(parent, &directory_meta)?;
        let name = super::audit_export::output_name(&archive)?;
        storage.unlink_archive(&directory, &name)?;
        storage.sync_directory(&directory)?;
        let completed = json!({
            "schema": "synveil.audit-retention-release/v1",
            "released": true,
            "authorization_durable": true,
            "algorithm": "blake2b-512",
            "digest": expected,
            "bytes": archived.len(),
            "records": inspected["records"],
            "authenticated": false,
        });
        same_private_file(authorization, &receipt.metadata()?)?;
        storage.write_receipt(&mut receipt, &completed)?;
        storage.sync_file(&receipt)?;
        Ok(completed)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        root: PathBuf,
        active: PathBuf,
        archive: PathBuf,
        copy: PathBuf,
        authorization: PathBuf,
        digest: String,
    }

    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "synveil-retention-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
            let copies = root.join("copies");
            fs::DirBuilder::new().mode(0o700).create(&copies).unwrap();
            let active = root.join("journal");
            let archive = root.join("journal.1");
            let text = format!("{}\n", super::super::audit::record("retained", json!({})));
            for path in [&active, &archive] {
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(path)
                    .unwrap();
                file.write_all(text.as_bytes()).unwrap();
            }
            let copy = copies.join("snapshot");
            // Other parallel tests fork; only pre-write lock contention is
            // retried. Every other failure remains visible immediately.
            for attempt in 0..=100 {
                match super::super::audit_export::export(&archive, &copy) {
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock && attempt < 100 => {
                        std::thread::sleep(std::time::Duration::from_millis(10));
                    }
                    result => {
                        result.unwrap();
                        break;
                    }
                }
            }
            Self {
                active,
                archive,
                copy,
                authorization: copies.join("release.json"),
                digest: super::super::audit_export::checksum(text.as_bytes()),
                root,
            }
        }

        fn release(&self) -> io::Result<Value> {
            release_archive(&self.active, &self.copy, &self.authorization, &self.digest)
        }

        fn release_after_contention(&self) -> Value {
            self.release_until_uncontended().unwrap()
        }

        fn release_until_uncontended(&self) -> io::Result<Value> {
            for attempt in 0..=100 {
                match self.release() {
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock && attempt < 100 => {
                        std::thread::sleep(std::time::Duration::from_millis(10));
                    }
                    result => return result,
                }
            }
            unreachable!()
        }

        fn unchanged(&self, archive: &[u8], active: &[u8]) {
            assert_eq!(fs::read(&self.archive).unwrap(), archive);
            assert_eq!(fs::read(&self.active).unwrap(), active);
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    #[test]
    fn release_preserves_active_copy_and_durable_authorization() {
        let fixture = Fixture::new();
        let active = fs::read(&fixture.active).unwrap();
        let active_inode = fs::metadata(&fixture.active).unwrap().ino();
        let copy = fs::read(&fixture.copy).unwrap();
        let report = fixture.release_after_contention();
        assert_eq!(report["released"], true);
        assert_eq!(report["authenticated"], false);
        assert!(!fixture.archive.exists());
        assert_eq!(fs::read(&fixture.active).unwrap(), active);
        assert_eq!(fs::metadata(&fixture.active).unwrap().ino(), active_inode);
        assert_eq!(fs::read(&fixture.copy).unwrap(), copy);
        let saved = fs::read_to_string(&fixture.authorization).unwrap();
        let receipts: Vec<Value> = saved
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(receipts.len(), 2);
        assert_eq!(receipts[0]["phase"], "authorized-before-unlink");
        assert_eq!(receipts[0]["digest"], fixture.digest);
        assert_eq!(receipts[1], report);
        assert_eq!(
            fs::metadata(&fixture.authorization).unwrap().mode() & 0o777,
            0o600
        );
        assert!(fixture.release().is_err());
        assert_eq!(fs::read(&fixture.copy).unwrap(), copy);
    }

    #[test]
    fn digest_copy_or_structure_mismatch_cannot_release_archive() {
        for kind in ["digest", "copy", "tail", "archive"] {
            let mut fixture = Fixture::new();
            match kind {
                "digest" => fixture.digest = "0".repeat(128),
                "copy" => fs::write(&fixture.copy, b"incomplete").unwrap(),
                "tail" => fs::write(&fixture.archive, b"incomplete").unwrap(),
                "archive" => {
                    let text = format!("{}\n", super::super::audit::record("other", json!({})));
                    fs::write(&fixture.archive, text).unwrap();
                }
                _ => unreachable!(),
            }
            let archive = fs::read(&fixture.archive).unwrap();
            let active = fs::read(&fixture.active).unwrap();
            assert!(fixture.release().is_err());
            fixture.unchanged(&archive, &active);
            assert!(!fixture.authorization.exists());
        }
    }

    #[test]
    fn unsafe_objects_and_existing_receipts_leave_history_intact() {
        for kind in [
            "archive-link",
            "copy-link",
            "receipt",
            "receipt-link",
            "mode",
            "same-dir",
        ] {
            let fixture = Fixture::new();
            let archived = fs::read(&fixture.archive).unwrap();
            let active = fs::read(&fixture.active).unwrap();
            let other = fixture.root.join("other");
            fs::write(&other, b"unrelated").unwrap();
            match kind {
                "archive-link" => {
                    fs::remove_file(&fixture.archive).unwrap();
                    std::os::unix::fs::symlink(&other, &fixture.archive).unwrap();
                }
                "copy-link" => {
                    fs::remove_file(&fixture.copy).unwrap();
                    std::os::unix::fs::symlink(&fixture.archive, &fixture.copy).unwrap();
                }
                "receipt" => fs::write(&fixture.authorization, b"existing").unwrap(),
                "receipt-link" => {
                    std::os::unix::fs::symlink(&other, &fixture.authorization).unwrap()
                }
                "mode" => fs::set_permissions(
                    fixture.copy.parent().unwrap(),
                    fs::Permissions::from_mode(0o755),
                )
                .unwrap(),
                "same-dir" => {
                    let result = release_archive(
                        &fixture.active,
                        &fixture.archive,
                        &fixture.root.join("intent"),
                        &fixture.digest,
                    );
                    assert!(result.is_err());
                }
                _ => unreachable!(),
            }
            if kind != "same-dir" {
                assert!(fixture.release().is_err());
            }
            if kind != "archive-link" {
                assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
            } else {
                assert!(
                    fs::symlink_metadata(&fixture.archive)
                        .unwrap()
                        .file_type()
                        .is_symlink()
                );
            }
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::read(&other).unwrap(), b"unrelated");
            if kind == "receipt" {
                assert_eq!(fs::read(&fixture.authorization).unwrap(), b"existing");
            }
        }
    }

    #[test]
    fn unsafe_or_invalid_active_journal_cannot_release_retained_history() {
        for kind in [
            "missing",
            "archive-symlink",
            "copy-symlink",
            "archive-hardlink",
            "copy-hardlink",
            "directory",
            "fifo",
            "mode",
            "partial",
            "invalid",
        ] {
            let fixture = Fixture::new();
            let archive = fs::read(&fixture.archive).unwrap();
            let copy = fs::read(&fixture.copy).unwrap();
            fs::remove_file(&fixture.active).unwrap();
            match kind {
                "missing" => {}
                "archive-symlink" => {
                    std::os::unix::fs::symlink(&fixture.archive, &fixture.active).unwrap()
                }
                "copy-symlink" => {
                    std::os::unix::fs::symlink(&fixture.copy, &fixture.active).unwrap()
                }
                "archive-hardlink" => fs::hard_link(&fixture.archive, &fixture.active).unwrap(),
                "copy-hardlink" => fs::hard_link(&fixture.copy, &fixture.active).unwrap(),
                "directory" => fs::create_dir(&fixture.active).unwrap(),
                "fifo" => {
                    assert!(
                        std::process::Command::new("mkfifo")
                            .arg(&fixture.active)
                            .status()
                            .unwrap()
                            .success()
                    );
                }
                _ => {
                    fs::write(
                        &fixture.active,
                        if kind == "mode" {
                            archive.as_slice()
                        } else if kind == "partial" {
                            b"partial".as_slice()
                        } else {
                            b"invalid\n".as_slice()
                        },
                    )
                    .unwrap();
                    fs::set_permissions(
                        &fixture.active,
                        fs::Permissions::from_mode(if kind == "mode" { 0o644 } else { 0o600 }),
                    )
                    .unwrap();
                }
            }
            let before = fs::symlink_metadata(&fixture.active).ok();
            assert!(fixture.release().is_err(), "accepted unsafe active: {kind}");
            assert_eq!(fs::read(&fixture.archive).unwrap(), archive);
            assert_eq!(fs::read(&fixture.copy).unwrap(), copy);
            assert!(!fixture.authorization.exists());
            match before {
                None => assert!(fs::symlink_metadata(&fixture.active).is_err()),
                Some(meta) => {
                    let now = fs::symlink_metadata(&fixture.active).unwrap();
                    assert_eq!(identity(&now), identity(&meta));
                    assert_eq!(now.file_type(), meta.file_type());
                    assert_eq!(now.len(), meta.len());
                    assert_eq!(now.mode(), meta.mode());
                }
            }
        }
    }

    struct FaultingRetentionIo {
        fail_at: usize,
        calls: Vec<&'static str>,
    }

    impl FaultingRetentionIo {
        fn step(&mut self, name: &'static str) -> io::Result<()> {
            self.calls.push(name);
            if self.calls.len() == self.fail_at {
                return Err(io::Error::from_raw_os_error(5));
            }
            Ok(())
        }
    }

    impl RetentionIo for FaultingRetentionIo {
        fn sync_file(&mut self, file: &fs::File) -> io::Result<()> {
            self.step("sync_file")?;
            FilesystemRetentionIo.sync_file(file)
        }
        fn sync_directory(&mut self, directory: &fs::File) -> io::Result<()> {
            self.step("sync_directory")?;
            FilesystemRetentionIo.sync_directory(directory)
        }
        fn create_receipt(&mut self, directory: &fs::File) -> io::Result<fs::File> {
            self.step("create_receipt")?;
            FilesystemRetentionIo.create_receipt(directory)
        }
        fn write_receipt(&mut self, file: &mut fs::File, record: &Value) -> io::Result<()> {
            self.step("write_receipt")?;
            FilesystemRetentionIo.write_receipt(file, record)
        }
        fn publish_receipt(
            &mut self,
            file: &fs::File,
            directory: &fs::File,
            name: &CString,
        ) -> io::Result<()> {
            self.step("publish_receipt")?;
            FilesystemRetentionIo.publish_receipt(file, directory, name)
        }
        fn unlink_archive(&mut self, directory: &fs::File, name: &CString) -> io::Result<()> {
            self.step("unlink_archive")?;
            FilesystemRetentionIo.unlink_archive(directory, name)
        }
    }

    #[test]
    fn storage_errors_stop_release_and_preserve_copy_active_and_truthful_receipts() {
        let operations = [
            "sync_file",
            "sync_directory",
            "create_receipt",
            "write_receipt",
            "sync_file",
            "publish_receipt",
            "sync_directory",
            "unlink_archive",
            "sync_directory",
            "write_receipt",
            "sync_file",
        ];
        for fail_at in 1..=operations.len() {
            let fixture = Fixture::new();
            let active = fs::read(&fixture.active).unwrap();
            let active_inode = fs::metadata(&fixture.active).unwrap().ino();
            let archived = fs::read(&fixture.archive).unwrap();
            let archive_inode = fs::metadata(&fixture.archive).unwrap().ino();
            let copy_inode = fs::metadata(&fixture.copy).unwrap().ino();
            let mut storage = FaultingRetentionIo {
                fail_at,
                calls: Vec::new(),
            };
            let mut result = release_with_io(
                &fixture.active,
                &fixture.copy,
                &fixture.authorization,
                &fixture.digest,
                &mut storage,
            );
            for _ in 0..100 {
                if !matches!(&result, Err(error) if error.kind() == io::ErrorKind::WouldBlock && storage.calls.is_empty())
                {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
                result = release_with_io(
                    &fixture.active,
                    &fixture.copy,
                    &fixture.authorization,
                    &fixture.digest,
                    &mut storage,
                );
            }
            assert_eq!(result.unwrap_err().raw_os_error(), Some(5));
            assert_eq!(storage.calls, operations[..fail_at]);
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::metadata(&fixture.active).unwrap().ino(), active_inode);
            assert_eq!(fs::read(&fixture.copy).unwrap(), archived);
            assert_eq!(fs::metadata(&fixture.copy).unwrap().ino(), copy_inode);
            if fail_at <= 8 {
                assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
                assert_eq!(fs::metadata(&fixture.archive).unwrap().ino(), archive_inode);
            } else {
                assert!(!fixture.archive.exists());
            }
            if fail_at >= 7 {
                let saved = fs::read_to_string(&fixture.authorization).unwrap();
                let records: Vec<Value> = saved
                    .lines()
                    .map(|line| serde_json::from_str(line).unwrap())
                    .collect();
                assert_eq!(records[0]["phase"], "authorized-before-unlink");
                // A final sync error can leave complete unacknowledged bytes.
                assert_eq!(records.len(), if fail_at == 11 { 2 } else { 1 });
                if records.len() == 2 {
                    assert_eq!(records[1]["released"], true);
                    assert!(!fixture.archive.exists());
                }
            } else {
                assert!(!fixture.authorization.exists());
            }
            if fail_at <= 8 {
                let retry = fixture.copy.with_file_name("retry.json");
                let mut done = false;
                for _ in 0..100 {
                    match release_archive(&fixture.active, &fixture.copy, &retry, &fixture.digest) {
                        Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                            std::thread::sleep(std::time::Duration::from_millis(10))
                        }
                        result => {
                            assert_eq!(result.unwrap()["released"], true);
                            done = true;
                            break;
                        }
                    }
                }
                assert!(done);
                assert!(!fixture.archive.exists());
            } else {
                let receipt = fs::read(&fixture.authorization).unwrap();
                assert_eq!(
                    fixture.release_until_uncontended().unwrap_err().kind(),
                    io::ErrorKind::NotFound
                );
                assert_eq!(fs::read(&fixture.authorization).unwrap(), receipt);
            }
        }
    }

    struct PartialReceiptIo {
        completion: bool,
        writes: usize,
        partial: Vec<u8>,
    }

    impl RetentionIo for PartialReceiptIo {
        fn write_receipt(&mut self, file: &mut fs::File, record: &Value) -> io::Result<()> {
            self.writes += 1;
            if self.writes == if self.completion { 2 } else { 1 } {
                self.partial = record.to_string().as_bytes()[..17].to_vec();
                file.write_all(&self.partial)?;
                return Err(io::Error::from_raw_os_error(5));
            }
            FilesystemRetentionIo.write_receipt(file, record)
        }
    }

    #[test]
    fn partial_receipt_writes_never_publish_a_false_completion_or_erase_history() {
        for completion in [false, true] {
            let fixture = Fixture::new();
            let archived = fs::read(&fixture.archive).unwrap();
            let active = fs::read(&fixture.active).unwrap();
            let mut storage = PartialReceiptIo {
                completion,
                writes: 0,
                partial: Vec::new(),
            };
            let mut result = release_with_io(
                &fixture.active,
                &fixture.copy,
                &fixture.authorization,
                &fixture.digest,
                &mut storage,
            );
            for _ in 0..100 {
                if !matches!(&result, Err(error) if error.kind() == io::ErrorKind::WouldBlock && storage.writes == 0)
                {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
                result = release_with_io(
                    &fixture.active,
                    &fixture.copy,
                    &fixture.authorization,
                    &fixture.digest,
                    &mut storage,
                );
            }
            assert_eq!(result.unwrap_err().raw_os_error(), Some(5));
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::read(&fixture.copy).unwrap(), archived);
            if completion {
                assert!(!fixture.archive.exists());
                let saved = fs::read(&fixture.authorization).unwrap();
                let split = saved.iter().position(|byte| *byte == b'\n').unwrap();
                let intent: Value = serde_json::from_slice(&saved[..split]).unwrap();
                assert_eq!(intent["phase"], "authorized-before-unlink");
                assert_eq!(&saved[split + 1..], storage.partial.as_slice());
                assert_eq!(saved.len() - split - 1, 17);
                assert!(!saved.ends_with(b"\n"));
                assert!(serde_json::from_slice::<Value>(&saved[split + 1..]).is_err());
            } else {
                assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
                assert!(!fixture.authorization.exists());
            }
        }
    }

    #[test]
    fn empty_active_is_preserved_while_verified_archive_is_released() {
        let fixture = Fixture::new();
        fs::write(&fixture.active, b"").unwrap();
        let inode = fs::metadata(&fixture.active).unwrap().ino();
        assert_eq!(fixture.release_after_contention()["released"], true);
        assert!(fs::read(&fixture.active).unwrap().is_empty());
        assert_eq!(fs::metadata(&fixture.active).unwrap().ino(), inode);
        assert!(!fixture.archive.exists());
    }

    #[test]
    fn busy_or_ambiguous_release_is_refused_before_publication() {
        let fixture = Fixture::new();
        let archived = fs::read(&fixture.archive).unwrap();
        let active = fs::read(&fixture.active).unwrap();
        let held = super::super::audit::directory_lock(&fixture.active, true).unwrap();
        assert_eq!(
            fixture.release().unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert!(!fixture.authorization.exists());
        drop(held);
        let held_active = fs::File::open(&fixture.active).unwrap();
        assert_eq!(unsafe { flock(held_active.as_raw_fd(), 1 | 4) }, 0);
        assert_eq!(
            fixture.release().unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert!(!fixture.authorization.exists());
        assert_eq!(unsafe { flock(held_active.as_raw_fd(), 8) }, 0);
        drop(held_active);
        for path in [
            Path::new("relative"),
            &fixture.archive,
            &fixture.root.join(".audit-lock"),
        ] {
            assert!(
                release_archive(path, &fixture.copy, &fixture.authorization, &fixture.digest)
                    .is_err()
            );
        }
        assert!(
            release_archive(
                &fixture.active,
                &fixture.copy,
                &fixture.copy,
                &fixture.digest
            )
            .is_err()
        );
        assert!(
            release_archive(
                &fixture.active,
                &fixture.copy,
                &fixture.copy.with_file_name(".audit-lock"),
                &fixture.digest
            )
            .is_err()
        );
        fixture.unchanged(&archived, &active);
        assert!(!fixture.authorization.exists());
    }
}
