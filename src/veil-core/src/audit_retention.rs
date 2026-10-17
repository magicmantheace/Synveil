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

/// Observe a release receipt and its retained snapshot without repairing either.
/// A recorded completion is not proof of durable storage or current archive state.
pub fn inspect_release(
    active: &Path,
    snapshot: &Path,
    authorization: &Path,
    expected: &str,
) -> io::Result<Value> {
    const MAX_RECEIPT_BYTES: u64 = 8192;
    if ![active, snapshot, authorization]
        .into_iter()
        .all(absolute_normal)
        || snapshot == authorization
        || snapshot.parent() != authorization.parent()
        || active.file_name().unwrap() == ".audit-lock"
        || active
            .file_name()
            .unwrap()
            .as_encoded_bytes()
            .ends_with(b".1")
        || authorization.file_name().unwrap() == ".audit-lock"
        || expected.len() != 128
        || !expected
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid inspection arguments",
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
    // Release always creates this lock before publishing intent. Inspection
    // never creates it, and shares it to exclude a cooperating live release.
    let _lock = super::audit::directory_lock(active, false)?.ok_or_else(refused)?;
    let receipt = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW_NONBLOCK)
        .open(authorization)?;
    let receipt_meta = receipt.metadata()?;
    same_private_file(authorization, &receipt_meta)?;
    if receipt_meta.len() > MAX_RECEIPT_BYTES {
        return Err(refused());
    }
    if unsafe { flock(receipt.as_raw_fd(), 1 | 4) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut bytes = Vec::new();
    (&receipt)
        .take(MAX_RECEIPT_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_RECEIPT_BYTES || bytes.contains(&b'\r') {
        return Err(refused());
    }
    let end = bytes.iter().position(|b| *b == b'\n').ok_or_else(refused)?;
    let intent = super::audit_json::decode(&bytes[..end]).map_err(|_| refused())?;
    super::audit_check::with_snapshot_file(snapshot, |copy, retained, inspected| {
        let copy_meta = copy.metadata()?;
        same_private_file(snapshot, &copy_meta)?;
        let keys = [
            "schema",
            "operation",
            "phase",
            "uid",
            "algorithm",
            "digest",
            "bytes",
            "records",
            "active_device",
            "active_inode",
            "archive_device",
            "archive_inode",
            "snapshot_device",
            "snapshot_inode",
            "authenticated",
        ];
        let exact_keys = intent.as_object().is_some_and(|object| {
            object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
        });
        if !exact_keys
            || retained.is_empty()
            || intent["schema"] != "synveil.audit-retention-authorization/v1"
            || intent["operation"] != "release-archive"
            || intent["phase"] != "authorized-before-unlink"
            || intent["uid"] != unsafe { geteuid() }
            || intent["algorithm"] != "blake2b-512"
            || intent["digest"] != expected
            || intent["bytes"] != retained.len()
            || intent["records"] != inspected["records"]
            || intent["snapshot_device"] != copy_meta.dev()
            || intent["snapshot_inode"] != copy_meta.ino()
            || intent["authenticated"] != false
            || [
                "active_device",
                "active_inode",
                "archive_device",
                "archive_inode",
            ]
            .iter()
            .any(|key| intent[*key].as_u64().is_none())
            || super::audit_export::checksum(retained) != expected
            || identity(&copy_meta) == identity(&receipt_meta)
        {
            return Err(refused());
        }
        let completion = json!({
            "schema": "synveil.audit-retention-release/v1",
            "released": true, "authorization_durable": true,
            "algorithm": "blake2b-512", "digest": expected,
            "bytes": retained.len(), "records": inspected["records"],
            "authenticated": false,
        });
        let tail = &bytes[end + 1..];
        let (state, recorded) = if tail.is_empty() {
            ("authorization-only", false)
        } else if tail.ends_with(b"\n") {
            if tail.iter().filter(|b| **b == b'\n').count() != 1 {
                return Err(refused());
            }
            let decoded = super::audit_json::decode(tail).map_err(|_| refused())?;
            if decoded != completion {
                return Err(refused());
            }
            ("completion-recorded", true)
        } else {
            // Only a prefix that the actual writer could have produced counts
            // as interrupted completion; arbitrary trailing bytes are refused.
            let encoded = format!("{completion}\n");
            if !encoded.as_bytes().starts_with(tail) {
                return Err(refused());
            }
            ("incomplete-completion", false)
        };
        same_private_file(authorization, &receipt_meta)?;
        same_private_file(snapshot, &copy_meta)?;
        same_directory(parent, &directory_meta)?;
        same_directory(copies_parent, &copies_meta)?;
        Ok(json!({
            "schema": "synveil.audit-release-inspection/v1",
            "state": state, "completion_recorded": recorded,
            "archive_state": "not-inspected", "authenticated": false,
            "algorithm": "blake2b-512", "digest": expected,
            "bytes": retained.len(), "records": inspected["records"],
            "archive_device": intent["archive_device"],
            "archive_inode": intent["archive_inode"],
        }))
    })
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

    // Parallel process-cut tests can inherit an open flock until their exec.
    // Retry only fixture lock acquisition; production inspection stays nonblocking.
    fn retry_lock<T>(mut operation: impl FnMut() -> io::Result<T>) -> io::Result<T> {
        for attempt in 0..=100 {
            match operation() {
                Err(error) if error.kind() == io::ErrorKind::WouldBlock && attempt < 100 => {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                result => return result,
            }
        }
        unreachable!()
    }

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

        fn inspect(&self) -> io::Result<Value> {
            for attempt in 0..=100 {
                match inspect_release(&self.active, &self.copy, &self.authorization, &self.digest) {
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock && attempt < 100 => {
                        std::thread::sleep(std::time::Duration::from_millis(10));
                    }
                    result => return result,
                }
            }
            unreachable!()
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
    fn inspection_distinguishes_recorded_phases_without_repair_or_active_history() {
        let fixture = Fixture::new();
        fixture.release_after_contention();
        let receipt = fs::read(&fixture.authorization).unwrap();
        let end = receipt.iter().position(|b| *b == b'\n').unwrap() + 1;
        let active = fs::read(&fixture.active).unwrap();
        let copy = fs::read(&fixture.copy).unwrap();
        for (size, state, recorded) in [
            (receipt.len(), "completion-recorded", true),
            (end, "authorization-only", false),
            (end + 17, "incomplete-completion", false),
            (receipt.len() - 1, "incomplete-completion", false),
        ] {
            fs::write(&fixture.authorization, &receipt[..size]).unwrap();
            let result = fixture.inspect().unwrap();
            assert_eq!(result["state"], state);
            assert_eq!(result["completion_recorded"], recorded);
            assert_eq!(result["archive_state"], "not-inspected");
            assert_eq!(result["authenticated"], false);
            assert_eq!(fs::read(&fixture.authorization).unwrap(), receipt[..size]);
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::read(&fixture.copy).unwrap(), copy);
            assert!(!fixture.archive.exists());
        }
        // An old completion does not establish the state of a reused slot.
        fs::copy(&fixture.copy, &fixture.archive).unwrap();
        fs::write(&fixture.authorization, &receipt).unwrap();
        fs::remove_file(&fixture.active).unwrap();
        assert_eq!(fixture.inspect().unwrap()["state"], "completion-recorded");
        assert_eq!(fs::read(&fixture.archive).unwrap(), copy);
        assert!(!fixture.active.exists());
    }

    #[test]
    fn inspection_refuses_ambiguous_mismatched_or_oversized_receipts() {
        let fixture = Fixture::new();
        fixture.release_after_contention();
        let receipt = fs::read(&fixture.authorization).unwrap();
        let mut lines = receipt.split_inclusive(|b| *b == b'\n');
        let first = lines.next().unwrap();
        let second = lines.next().unwrap();
        let intent: Value = serde_json::from_slice(first).unwrap();
        let completion: Value = serde_json::from_slice(second).unwrap();
        let mut cases = vec![
            Vec::new(),
            first[..first.len() - 1].to_vec(),
            [first, b"garbage"].concat(),
            [receipt.as_slice(), b"\n"].concat(),
            [receipt.as_slice(), second].concat(),
            vec![b'x'; 8193],
            [
                b"{\"phase\":\"authorized-before-unlink\",",
                &first[1..],
                second,
            ]
            .concat(),
            [first, b"{\"released\":true,", &second[1..]].concat(),
        ];
        for (key, value) in [
            ("schema", json!("unknown")),
            ("operation", json!("other")),
            ("phase", json!("completed")),
            ("uid", json!(u64::MAX)),
            ("algorithm", json!("other")),
            ("digest", json!("0".repeat(128))),
            ("bytes", json!(0)),
            ("records", json!(0)),
            ("snapshot_inode", json!(u64::MAX)),
            ("snapshot_device", json!(u64::MAX)),
            ("active_inode", json!("invalid")),
            ("authenticated", json!(true)),
            ("extra", json!(false)),
        ] {
            let mut invalid = intent.clone();
            invalid[key] = value;
            cases.push(format!("{invalid}\n{completion}\n").into_bytes());
        }
        let mut missing = intent.clone();
        missing.as_object_mut().unwrap().remove("archive_inode");
        cases.push(format!("{missing}\n{completion}\n").into_bytes());
        for (key, value) in [
            ("released", json!(false)),
            ("authorization_durable", json!(false)),
            ("digest", json!("0".repeat(128))),
            ("records", json!(2)),
            ("authenticated", json!(true)),
            ("extra", json!(false)),
        ] {
            let mut invalid = completion.clone();
            invalid[key] = value;
            cases.push(format!("{intent}\n{invalid}\n").into_bytes());
        }
        for invalid in cases {
            fs::write(&fixture.authorization, &invalid).unwrap();
            assert!(fixture.inspect().is_err());
            assert_eq!(fs::read(&fixture.authorization).unwrap(), invalid);
        }
        fs::write(&fixture.authorization, &receipt).unwrap();
        // Valid journal bytes with a different checksum cannot validate intent.
        fs::write(
            &fixture.copy,
            format!("{}\n", super::super::audit::record("changed", json!({}))),
        )
        .unwrap();
        assert!(fixture.inspect().is_err());
    }

    #[test]
    fn inspection_refuses_unsafe_receipts_busy_locks_and_missing_scope_without_creating_it() {
        use std::os::unix::fs::symlink;
        let fixture = Fixture::new();
        fixture.release_after_contention();
        let receipt = fs::read(&fixture.authorization).unwrap();
        for path in [&fixture.authorization, &fixture.copy] {
            let held = fs::File::open(path).unwrap();
            retry_lock(|| {
                if unsafe { flock(held.as_raw_fd(), 2 | 4) } == 0 {
                    Ok(())
                } else {
                    Err(io::Error::last_os_error())
                }
            })
            .unwrap();
            assert_eq!(
                inspect_release(
                    &fixture.active,
                    &fixture.copy,
                    &fixture.authorization,
                    &fixture.digest
                )
                .unwrap_err()
                .kind(),
                io::ErrorKind::WouldBlock
            );
            assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        }
        let held = retry_lock(|| super::super::audit::directory_lock(&fixture.active, true))
            .unwrap()
            .unwrap();
        assert_eq!(
            inspect_release(
                &fixture.active,
                &fixture.copy,
                &fixture.authorization,
                &fixture.digest
            )
            .unwrap_err()
            .kind(),
            io::ErrorKind::WouldBlock
        );
        assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        drop(held);
        let held = fixture.authorization.with_file_name("held");
        fs::rename(&fixture.authorization, &held).unwrap();
        symlink(&held, &fixture.authorization).unwrap();
        assert!(fixture.inspect().is_err());
        fs::remove_file(&fixture.authorization).unwrap();
        fs::hard_link(&held, &fixture.authorization).unwrap();
        assert!(fixture.inspect().is_err());
        fs::remove_file(&fixture.authorization).unwrap();
        fs::rename(&held, &fixture.authorization).unwrap();
        fs::set_permissions(&fixture.authorization, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(fixture.inspect().is_err());
        fs::set_permissions(&fixture.authorization, fs::Permissions::from_mode(0o600)).unwrap();
        let lock_path = fixture.root.join(".audit-lock");
        fs::remove_file(&lock_path).unwrap();
        assert!(fixture.inspect().is_err());
        assert!(!lock_path.exists());
        assert_eq!(fs::read(&fixture.authorization).unwrap(), receipt);
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

    const RELEASE_CUTS: [&str; 9] = [
        "partial-intent",
        "intent-synced",
        "published",
        "authorization-synced",
        "unlinked",
        "unlink-synced",
        "partial-completion",
        "completion-written",
        "completion-synced",
    ];

    const RELEASE_WORKER_TIMEOUT_SECONDS: u64 = 5;

    fn release_cut(phase: &str) -> ! {
        unsafe extern "C" {
            fn raise(signal: i32) -> i32;
        }
        // --nocapture makes the fixed checkpoint observable before SIGKILL.
        eprintln!("SYNVEIL_RETENTION_CUT={phase}");
        unsafe { raise(9) };
        panic!("release worker survived SIGKILL");
    }

    struct ReleaseCrashIo {
        phase: String,
        file_syncs: usize,
        directory_syncs: usize,
        writes: usize,
    }

    impl RetentionIo for ReleaseCrashIo {
        fn sync_file(&mut self, file: &fs::File) -> io::Result<()> {
            FilesystemRetentionIo.sync_file(file)?;
            self.file_syncs += 1;
            if (self.file_syncs == 2 && self.phase == "intent-synced")
                || (self.file_syncs == 3 && self.phase == "completion-synced")
            {
                release_cut(&self.phase);
            }
            Ok(())
        }

        fn sync_directory(&mut self, directory: &fs::File) -> io::Result<()> {
            FilesystemRetentionIo.sync_directory(directory)?;
            self.directory_syncs += 1;
            if (self.directory_syncs == 2 && self.phase == "authorization-synced")
                || (self.directory_syncs == 3 && self.phase == "unlink-synced")
            {
                release_cut(&self.phase);
            }
            Ok(())
        }

        fn write_receipt(&mut self, file: &mut fs::File, record: &Value) -> io::Result<()> {
            self.writes += 1;
            if (self.writes == 1 && self.phase == "partial-intent")
                || (self.writes == 2 && self.phase == "partial-completion")
            {
                file.write_all(&record.to_string().as_bytes()[..17])?;
                release_cut(&self.phase);
            }
            FilesystemRetentionIo.write_receipt(file, record)?;
            if self.writes == 2 && self.phase == "completion-written" {
                release_cut(&self.phase);
            }
            Ok(())
        }

        fn publish_receipt(
            &mut self,
            file: &fs::File,
            directory: &fs::File,
            name: &CString,
        ) -> io::Result<()> {
            FilesystemRetentionIo.publish_receipt(file, directory, name)?;
            if self.phase == "published" {
                release_cut(&self.phase);
            }
            Ok(())
        }

        fn unlink_archive(&mut self, directory: &fs::File, name: &CString) -> io::Result<()> {
            FilesystemRetentionIo.unlink_archive(directory, name)?;
            if self.phase == "unlinked" {
                release_cut(&self.phase);
            }
            Ok(())
        }
    }

    fn wait_release_worker(
        mut child: std::process::Child,
        seconds: u64,
        whole_group: bool,
    ) -> (std::process::ExitStatus, String) {
        use std::time::{Duration, Instant};
        let deadline = Instant::now() + Duration::from_secs(seconds);
        let status = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break status;
            }
            if Instant::now() >= deadline {
                if whole_group {
                    unsafe extern "C" {
                        fn kill(pid: i32, signal: i32) -> i32;
                    }
                    // The outer worker owns a fresh process group; its
                    // descendants must not survive an outer watchdog failure.
                    let pid = i32::try_from(child.id()).unwrap();
                    if unsafe { kill(-pid, 9) } != 0 {
                        // A worker can exit between try_wait and the group
                        // signal. Still reap it before reporting the timeout.
                        let _ = child.kill();
                    }
                } else {
                    child.kill().unwrap();
                }
                child.wait().unwrap();
                panic!("release test worker timed out");
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
        (status, diagnostic)
    }

    #[test]
    fn release_crash_worker() {
        let Some(root) = std::env::var_os("SYNVEIL_TEST_RELEASE_CRASH_ROOT") else {
            return;
        };
        let root = PathBuf::from(root);
        assert_eq!(root.parent().unwrap(), std::env::temp_dir());
        assert!(
            root.file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with("synveil-retention-")
        );
        private_directory(&root).unwrap();
        let phase = std::env::var("SYNVEIL_TEST_RELEASE_CRASH_PHASE").unwrap();
        assert!(RELEASE_CUTS.contains(&phase.as_str()));
        let digest = std::env::var("SYNVEIL_TEST_RELEASE_CRASH_DIGEST").unwrap();
        let mut storage = ReleaseCrashIo {
            phase,
            file_syncs: 0,
            directory_syncs: 0,
            writes: 0,
        };
        release_with_io(
            &root.join("journal"),
            &root.join("copies/snapshot"),
            &root.join("copies/release.json"),
            &digest,
            &mut storage,
        )
        .unwrap();
        panic!("release crash boundary was not reached");
    }

    #[test]
    fn release_crash_matrix_worker() {
        use std::os::unix::process::ExitStatusExt;
        use std::process::{Command, Stdio};
        let Some(enabled) = std::env::var_os("SYNVEIL_TEST_RELEASE_CRASH_MATRIX") else {
            return;
        };
        assert_eq!(enabled, "1");
        for phase in RELEASE_CUTS {
            let fixture = Fixture::new();
            let active = fs::read(&fixture.active).unwrap();
            let archived = fs::read(&fixture.archive).unwrap();
            let active_inode = fs::metadata(&fixture.active).unwrap().ino();
            let archive_inode = fs::metadata(&fixture.archive).unwrap().ino();
            let copy_inode = fs::metadata(&fixture.copy).unwrap().ino();
            // Synchronize only the seed, before the cut worker starts.
            for path in [&fixture.active, &fixture.archive, &fixture.copy] {
                fs::File::open(path).unwrap().sync_all().unwrap();
            }
            fs::File::open(&fixture.root).unwrap().sync_all().unwrap();
            fs::File::open(fixture.copy.parent().unwrap())
                .unwrap()
                .sync_all()
                .unwrap();
            let child = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "audit_retention::tests::release_crash_worker",
                    "--nocapture",
                    "--test-threads=1",
                ])
                .env("SYNVEIL_TEST_RELEASE_CRASH_ROOT", &fixture.root)
                .env("SYNVEIL_TEST_RELEASE_CRASH_PHASE", phase)
                .env("SYNVEIL_TEST_RELEASE_CRASH_DIGEST", &fixture.digest)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::piped())
                .spawn()
                .unwrap();
            let (status, diagnostic) =
                wait_release_worker(child, RELEASE_WORKER_TIMEOUT_SECONDS, false);
            assert_eq!(status.signal(), Some(9), "{phase}: {diagnostic}");
            let checkpoints: Vec<_> = diagnostic
                .lines()
                .filter(|line| line.starts_with("SYNVEIL_RETENTION_CUT="))
                .collect();
            let checkpoint = format!("SYNVEIL_RETENTION_CUT={phase}");
            assert_eq!(checkpoints, [checkpoint.as_str()]);
            // Observe interrupted state before any post-cut sync or recovery.
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::metadata(&fixture.active).unwrap().ino(), active_inode);
            assert_eq!(fs::read(&fixture.copy).unwrap(), archived);
            assert_eq!(fs::metadata(&fixture.copy).unwrap().ino(), copy_inode);
            let removed = [
                "unlinked",
                "unlink-synced",
                "partial-completion",
                "completion-written",
                "completion-synced",
            ]
            .contains(&phase);
            if removed {
                assert!(!fixture.archive.exists());
            } else {
                assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
                assert_eq!(fs::metadata(&fixture.archive).unwrap().ino(), archive_inode);
            }
            let saved = fs::read(&fixture.authorization).ok();
            let anonymous = ["partial-intent", "intent-synced"].contains(&phase);
            if anonymous {
                assert!(saved.is_none());
            } else {
                let bytes = saved.as_ref().unwrap();
                let split = bytes.iter().position(|byte| *byte == b'\n').unwrap();
                let intent: Value = serde_json::from_slice(&bytes[..split]).unwrap();
                assert_eq!(intent["phase"], "authorized-before-unlink");
                assert_eq!(intent["digest"], fixture.digest);
                assert_eq!(intent["archive_inode"], archive_inode);
                assert_eq!(intent["active_inode"], active_inode);
                assert_eq!(intent["snapshot_inode"], copy_inode);
                let tail = &bytes[split + 1..];
                if phase == "partial-completion" {
                    assert_eq!(tail.len(), 17);
                    assert!(!tail.ends_with(b"\n"));
                    assert!(serde_json::from_slice::<Value>(tail).is_err());
                } else if ["completion-written", "completion-synced"].contains(&phase) {
                    assert!(tail.ends_with(b"\n"));
                    let completion: Value = serde_json::from_slice(tail).unwrap();
                    assert_eq!(completion["released"], true);
                    assert_eq!(completion["digest"], fixture.digest);
                    assert!(removed);
                } else {
                    assert!(tail.is_empty());
                }
            }
            let mut entries: Vec<_> = fs::read_dir(fixture.copy.parent().unwrap())
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect();
            entries.sort();
            let expected: Vec<std::ffi::OsString> = if anonymous {
                vec!["snapshot".into()]
            } else {
                vec!["release.json".into(), "snapshot".into()]
            };
            assert_eq!(entries, expected);
            // Reaping must release every kernel lock, including stable scope.
            let held = super::super::audit::directory_lock(&fixture.active, true)
                .unwrap()
                .unwrap();
            assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
            drop(held);
            for path in [&fixture.active, &fixture.copy, &fixture.archive] {
                if path == &fixture.archive && removed {
                    continue;
                }
                let file = fs::File::open(path).unwrap();
                assert_eq!(unsafe { flock(file.as_raw_fd(), 2 | 4) }, 0);
                assert_eq!(unsafe { flock(file.as_raw_fd(), 8) }, 0);
            }
            super::super::audit_export::verify(&fixture.copy, &fixture.digest).unwrap();
            if anonymous {
                assert_eq!(
                    fixture.inspect().unwrap_err().kind(),
                    io::ErrorKind::NotFound
                );
            } else {
                let report = fixture.inspect().unwrap();
                let expected_state = match phase {
                    "partial-completion" => "incomplete-completion",
                    "completion-written" | "completion-synced" => "completion-recorded",
                    _ => "authorization-only",
                };
                assert_eq!(report["state"], expected_state);
                assert_eq!(report["archive_state"], "not-inspected");
                assert_eq!(fs::read(&fixture.authorization).ok(), saved);
            }
            if removed {
                assert_eq!(
                    fixture.release().unwrap_err().kind(),
                    io::ErrorKind::NotFound
                );
            } else {
                let fresh = fixture.copy.with_file_name("recovered.json");
                assert_eq!(
                    release_archive(&fixture.active, &fixture.copy, &fresh, &fixture.digest)
                        .unwrap()["released"],
                    true
                );
                assert!(!fixture.archive.exists());
            }
            assert_eq!(fs::read(&fixture.authorization).ok(), saved);
            assert_eq!(fs::read(&fixture.active).unwrap(), active);
            assert_eq!(fs::read(&fixture.copy).unwrap(), archived);
        }
        eprintln!("SYNVEIL_RETENTION_CRASH_MATRIX_OK");
    }

    #[test]
    fn process_loss_during_release_preserves_history_receipts_and_restart_safety() {
        use std::os::unix::process::CommandExt;
        use std::process::{Command, Stdio};
        // Isolate the matrix from parallel tests so its nine forks cannot
        // inherit their audit descriptors before exec.
        let child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "audit_retention::tests::release_crash_matrix_worker",
                "--nocapture",
                "--test-threads=1",
            ])
            .env("SYNVEIL_TEST_RELEASE_CRASH_MATRIX", "1")
            .env_remove("SYNVEIL_TEST_RELEASE_CRASH_ROOT")
            .process_group(0)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        // Cover every sequential worker's budget plus baseline/recovery I/O.
        let seconds = RELEASE_CUTS.len() as u64 * RELEASE_WORKER_TIMEOUT_SECONDS + 30;
        let (status, diagnostic) = wait_release_worker(child, seconds, true);
        assert!(
            status.success(),
            "isolated release matrix failed: {diagnostic}"
        );
        assert_eq!(
            diagnostic
                .lines()
                .filter(|line| *line == "SYNVEIL_RETENTION_CRASH_MATRIX_OK")
                .count(),
            1
        );
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
