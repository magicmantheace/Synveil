// SPDX-License-Identifier: MPL-2.0
//! Explicit offline archive release after durable copy and intent publication.
use serde_json::{Value, json};
use std::ffi::c_char;
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
    io::Error::new(io::ErrorKind::PermissionDenied, "unsafe audit retention input")
}

fn absolute_normal(path: &Path) -> bool {
    path.is_absolute()
        && path.file_name().is_some()
        && path.components().all(|part| {
            matches!(part, Component::RootDir | Component::Normal(_))
        })
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
    if ![active, snapshot, authorization].into_iter().all(absolute_normal)
        || active.file_name().unwrap() == ".audit-lock"
        || active.file_name().unwrap().as_encoded_bytes().ends_with(b".1")
        || snapshot == authorization
        || authorization.file_name().unwrap() == ".audit-lock"
        || snapshot.parent() != authorization.parent()
        || expected.len() != 128
        || !expected.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid release arguments"));
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
    let mut name = active.file_name().unwrap().to_os_string();
    name.push(".1");
    let archive = active.with_file_name(name);
    let archive_file = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW_NONBLOCK)
        .open(&archive)?;
    let archive_meta = archive_file.metadata()?;
    same_private_file(&archive, &archive_meta)?;
    if unsafe { flock(archive_file.as_raw_fd(), 2 | 4) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut archived = Vec::new();
    (&archive_file).take(MAX_BYTES + 1).read_to_end(&mut archived)?;
    let inspected = super::audit_check::inspect_bytes(&archived)?;
    if archived.is_empty() || super::audit_export::checksum(&archived) != expected {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "archive digest mismatch"));
    }
    super::audit_check::with_snapshot_file(snapshot, |copy, bytes, _| {
        let copy_meta = copy.metadata()?;
        same_private_file(snapshot, &copy_meta)?;
        if bytes != archived.as_slice() || identity(&copy_meta) == identity(&archive_meta) {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "snapshot mismatch"));
        }
        // Re-establish copy durability before publishing any release intent.
        copy.sync_all()?;
        copies_directory.sync_all()?;
        let intent = json!({
            "schema": "synveil.audit-retention-authorization/v1",
            "operation": "release-archive",
            "phase": "authorized-before-unlink",
            "uid": unsafe { geteuid() },
            "algorithm": "blake2b-512",
            "digest": expected,
            "bytes": archived.len(),
            "records": inspected["records"],
            "archive_device": archive_meta.dev(),
            "archive_inode": archive_meta.ino(),
            "snapshot_device": copy_meta.dev(),
            "snapshot_inode": copy_meta.ino(),
            "authenticated": false,
        });
        let mut receipt = super::audit_export::create_output(&copies_directory)?;
        writeln!(receipt, "{intent}")?;
        receipt.sync_all()?;
        same_directory(copies_parent, &copies_meta)?;
        super::audit_export::publish_output(
            &receipt,
            &copies_directory,
            &super::audit_export::output_name(authorization)?,
        )?;
        copies_directory.sync_all()?;
        // A collision or validation error leaves every journal intact. An
        // intent may remain after failure; no cleanup unlinks by pathname.
        same_private_file(authorization, &receipt.metadata()?)?;
        same_private_file(snapshot, &copy_meta)?;
        same_private_file(&archive, &archive_meta)?;
        same_directory(copies_parent, &copies_meta)?;
        same_directory(parent, &directory_meta)?;
        let name = super::audit_export::output_name(&archive)?;
        if unsafe { unlinkat(directory.as_raw_fd(), name.as_ptr(), 0) } != 0 {
            return Err(io::Error::last_os_error());
        }
        directory.sync_all()?;
        Ok(json!({
            "schema": "synveil.audit-retention-release/v1",
            "released": true,
            "authorization_durable": true,
            "algorithm": "blake2b-512",
            "digest": expected,
            "bytes": archived.len(),
            "records": inspected["records"],
            "authenticated": false,
        }))
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
            for attempt in 0..=100 {
                match self.release() {
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock && attempt < 100 => {
                        std::thread::sleep(std::time::Duration::from_millis(10));
                    }
                    result => return result.unwrap(),
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
        let receipt: Value = serde_json::from_slice(&fs::read(&fixture.authorization).unwrap()).unwrap();
        assert_eq!(receipt["phase"], "authorized-before-unlink");
        assert_eq!(receipt["digest"], fixture.digest);
        assert_eq!(fs::metadata(&fixture.authorization).unwrap().mode() & 0o777, 0o600);
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
        for kind in ["archive-link", "copy-link", "receipt", "receipt-link", "mode", "same-dir"] {
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
                "receipt-link" => std::os::unix::fs::symlink(&other, &fixture.authorization).unwrap(),
                "mode" => fs::set_permissions(fixture.copy.parent().unwrap(), fs::Permissions::from_mode(0o755)).unwrap(),
                "same-dir" => {
                    let result = release_archive(&fixture.active, &fixture.archive, &fixture.root.join("intent"), &fixture.digest);
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
                assert!(fs::symlink_metadata(&fixture.archive).unwrap().file_type().is_symlink());
            }
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::read(&other).unwrap(), b"unrelated");
            if kind == "receipt" {
                assert_eq!(fs::read(&fixture.authorization).unwrap(), b"existing");
            }
        }
    }

    #[test]
    fn busy_or_ambiguous_release_is_refused_before_publication() {
        let fixture = Fixture::new();
        let archived = fs::read(&fixture.archive).unwrap();
        let active = fs::read(&fixture.active).unwrap();
        let held = super::super::audit::directory_lock(&fixture.active, true).unwrap();
        assert_eq!(fixture.release().unwrap_err().kind(), io::ErrorKind::WouldBlock);
        assert!(!fixture.authorization.exists());
        drop(held);
        for path in [Path::new("relative"), &fixture.archive, &fixture.root.join(".audit-lock")] {
            assert!(release_archive(path, &fixture.copy, &fixture.authorization, &fixture.digest).is_err());
        }
        assert!(release_archive(&fixture.active, &fixture.copy, &fixture.copy, &fixture.digest).is_err());
        assert!(release_archive(&fixture.active, &fixture.copy, &fixture.copy.with_file_name(".audit-lock"), &fixture.digest).is_err());
        fixture.unchanged(&archived, &active);
        assert!(!fixture.authorization.exists());
    }
}
