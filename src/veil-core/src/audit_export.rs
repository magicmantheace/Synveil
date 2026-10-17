// SPDX-License-Identifier: MPL-2.0
//! Owner-controlled offline copies. Checksums do not authenticate event origin.
use blake2::{Blake2b512, Digest};
use serde_json::{Value, json};
use std::ffi::{CString, c_char};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::io::{AsRawFd, FromRawFd};
use std::path::Path;

const NOFOLLOW_NONBLOCK: i32 = 0o400000 | 0o4000;

unsafe extern "C" {
    fn geteuid() -> u32;
    fn openat(directory: i32, name: *const c_char, flags: i32, ...) -> i32;
    fn linkat(
        source_directory: i32,
        source: *const c_char,
        directory: i32,
        name: *const c_char,
        flags: i32,
    ) -> i32;
}

pub(crate) fn output_name(output: &Path) -> io::Result<CString> {
    let name = output.file_name().ok_or_else(unsafe_output)?;
    CString::new(name.as_bytes()).map_err(|_| unsafe_output())
}

pub(crate) fn create_output(directory: &fs::File) -> io::Result<fs::File> {
    // Linux O_RDWR | O_TMPFILE | O_CLOEXEC. No pathname exists before publication.
    let flags = 2 | 0o20200000 | 0o2000000;
    let fd = unsafe { openat(directory.as_raw_fd(), c".".as_ptr(), flags, 0o600_u32) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // Ownership of this newly allocated descriptor transfers to File once.
    Ok(unsafe { fs::File::from_raw_fd(fd) })
}

pub(crate) fn publish_output(
    file: &fs::File,
    directory: &fs::File,
    name: &CString,
) -> io::Result<()> {
    // The procfs descriptor link permits unprivileged publication of O_TMPFILE.
    // linkat creates the destination atomically and refuses every existing entry.
    let source = CString::new(format!("/proc/self/fd/{}", file.as_raw_fd())).unwrap();
    if unsafe {
        linkat(
            -100,
            source.as_ptr(),
            directory.as_raw_fd(),
            name.as_ptr(),
            0x400,
        )
    } < 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub(crate) fn checksum(bytes: &[u8]) -> String {
    format!("{:x}", Blake2b512::digest(bytes))
}

fn receipt(bytes: &[u8], inspected: &Value, schema: &str) -> Value {
    json!({
        "schema": schema,
        "record_schema": super::audit::AUDIT_SCHEMA_V1,
        "bytes": bytes.len(),
        "records": inspected["records"],
        "algorithm": "blake2b-512",
        "digest": checksum(bytes),
        "authenticated": false,
    })
}

/// Copy a validated, bounded snapshot while holding the inspector's locks.
/// Output must be new and in a separate, private directory owned by the caller.
pub fn export(source: &Path, output: &Path) -> io::Result<Value> {
    super::audit_check::with_snapshot(source, |bytes, inspected| {
        let parent = output.parent().ok_or_else(unsafe_output)?;
        if !output.is_absolute() || output.file_name().is_none() {
            return Err(unsafe_output());
        }
        let directory = OpenOptions::new()
            .read(true)
            .custom_flags(NOFOLLOW_NONBLOCK)
            .open(parent)?;
        let meta = directory.metadata()?;
        let source_parent = fs::metadata(source.parent().unwrap_or(Path::new(".")))?;
        if !meta.is_dir()
            || meta.uid() != unsafe { geteuid() }
            || meta.mode() & 0o777 != 0o700
            || (meta.dev(), meta.ino()) == (source_parent.dev(), source_parent.ino())
        {
            return Err(unsafe_output());
        }
        let name = output_name(output)?;
        let mut file = create_output(&directory)?;
        let created = file.metadata()?;
        if !created.is_file()
            || created.uid() != meta.uid()
            || created.nlink() != 0
            || created.mode() & 0o777 != 0o600
        {
            return Err(unsafe_output());
        }
        file.write_all(bytes)?;
        file.sync_all()?;
        file.seek(SeekFrom::Start(0))?;
        let mut copied = Vec::new();
        (&file)
            .take(bytes.len() as u64 + 1)
            .read_to_end(&mut copied)?;
        if copied.as_slice() != bytes {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "copy mismatch"));
        }
        // Validate the parent before publication as well as afterward.
        let parent_before = fs::symlink_metadata(parent)?;
        if (parent_before.dev(), parent_before.ino()) != (meta.dev(), meta.ino())
            || parent_before.mode() & 0o777 != 0o700
        {
            return Err(unsafe_output());
        }
        publish_output(&file, &directory, &name)?;
        directory.sync_all()?;
        let now = fs::symlink_metadata(output)?;
        let parent_now = fs::symlink_metadata(parent)?;
        if (now.dev(), now.ino()) != (created.dev(), created.ino())
            || (parent_now.dev(), parent_now.ino()) != (meta.dev(), meta.ino())
            || now.nlink() != 1
            || now.mode() & 0o777 != 0o600
            || now.len() != bytes.len() as u64
            || parent_now.mode() & 0o777 != 0o700
        {
            return Err(unsafe_output());
        }
        // After publication, an error may leave the complete copy. Never unlink
        // by pathname: another process could have replaced that entry.
        Ok(receipt(bytes, &inspected, "synveil.audit-export/v1"))
    })
}

fn unsafe_output() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "unsafe audit export output",
    )
}

/// Verify exact bytes against a separately retained owner-supplied digest.
pub fn verify(path: &Path, expected: &str) -> io::Result<Value> {
    if expected.len() != 128
        || !expected
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid digest",
        ));
    }
    super::audit_check::with_snapshot(path, |bytes, inspected| {
        let report = receipt(bytes, &inspected, "synveil.audit-verification/v1");
        if report["digest"] != expected {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "digest mismatch",
            ));
        }
        Ok(report)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::DirBuilderExt;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    fn fixture() -> (std::path::PathBuf, std::path::PathBuf, std::path::PathBuf) {
        let root = std::env::temp_dir().join(format!(
            "synveil-export-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
        let dest = root.join("copies");
        fs::DirBuilder::new().mode(0o700).create(&dest).unwrap();
        let source = root.join("journal");
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&source)
            .unwrap();
        writeln!(
            file,
            "{}",
            super::super::audit::record("request", json!({"private": "secret"}))
        )
        .unwrap();
        (root, source, dest.join("snapshot"))
    }

    #[test]
    fn copy_is_private_exact_and_verifiable_without_disclosing_fields() {
        let (root, source, output) = fixture();
        let original = fs::read(&source).unwrap();
        let report = export(&source, &output).unwrap();
        assert_eq!(fs::read(&output).unwrap(), original);
        assert_eq!(fs::read(&source).unwrap(), original);
        assert_eq!(fs::metadata(&output).unwrap().mode() & 0o777, 0o600);
        assert_eq!(report["authenticated"], false);
        assert!(!report.to_string().contains("secret"));
        verify(&output, report["digest"].as_str().unwrap()).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn well_formed_tampering_is_detected() {
        let (root, source, output) = fixture();
        let report = export(&source, &output).unwrap();
        let text = fs::read_to_string(&output)
            .unwrap()
            .replace("secret", "edited");
        fs::write(&output, text).unwrap();
        assert!(super::super::audit_check::inspect(&output).is_ok());
        assert_eq!(
            verify(&output, report["digest"].as_str().unwrap())
                .unwrap_err()
                .kind(),
            io::ErrorKind::InvalidData
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn existing_outputs_symlinks_and_source_directory_are_refused() {
        let (root, source, output) = fixture();
        fs::write(&output, b"existing").unwrap();
        assert!(export(&source, &output).is_err());
        assert_eq!(fs::read(&output).unwrap(), b"existing");
        fs::remove_file(&output).unwrap();
        std::os::unix::fs::symlink(&source, &output).unwrap();
        assert!(export(&source, &output).is_err());
        assert!(export(&source, &root.join("another-journal")).is_err());
        assert!(!root.join("another-journal").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn invalid_source_and_unsafe_destination_create_nothing() {
        use std::os::unix::fs::PermissionsExt;
        let (root, source, output) = fixture();
        fs::set_permissions(output.parent().unwrap(), fs::Permissions::from_mode(0o755)).unwrap();
        assert!(export(&source, &output).is_err());
        fs::set_permissions(output.parent().unwrap(), fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(&source, b"incomplete").unwrap();
        assert!(export(&source, &output).is_err());
        assert!(!output.exists());
        assert_eq!(fs::read(&source).unwrap(), b"incomplete");
        for digest in ["", "../bad", &"A".repeat(128)] {
            assert_eq!(
                verify(&source, digest).unwrap_err().kind(),
                io::ErrorKind::InvalidInput
            );
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn blake2b_digest_matches_published_vector() {
        assert_eq!(
            checksum(b"hello world"),
            concat!(
                "021ced8799296ceca557832ab941a50b4a11f83478cf141f51f933f653ab9fbc",
                "c05a037cddbed06e309bf334942c4e58cdf1a46e237911ccd7fcf9787cbc7fd0"
            )
        );
    }

    #[test]
    fn busy_source_refuses_export_without_creating_output() {
        use std::os::unix::io::AsRawFd;
        unsafe extern "C" {
            fn flock(fd: i32, operation: i32) -> i32;
        }
        let (root, source, output) = fixture();
        let held = super::super::audit::directory_lock(&source, true)
            .unwrap()
            .unwrap();
        assert_eq!(
            export(&source, &output).unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert!(!output.exists());
        assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        drop(held);
        export(&source, &output).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn publication_stays_in_open_directory_after_path_substitution() {
        let (root, _source, output) = fixture();
        let parent = output.parent().unwrap();
        let directory = fs::File::open(parent).unwrap();
        let moved = root.join("moved");
        let unrelated = root.join("unrelated");
        fs::DirBuilder::new()
            .mode(0o700)
            .create(&unrelated)
            .unwrap();
        let mut copy = create_output(&directory).unwrap();
        copy.write_all(b"private snapshot").unwrap();
        assert!(!output.exists());
        assert_eq!(copy.metadata().unwrap().nlink(), 0);
        fs::rename(parent, &moved).unwrap();
        std::os::unix::fs::symlink(&unrelated, parent).unwrap();
        publish_output(&copy, &directory, &output_name(&output).unwrap()).unwrap();
        assert_eq!(
            fs::read(moved.join("snapshot")).unwrap(),
            b"private snapshot"
        );
        assert!(!unrelated.join("snapshot").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn failed_publication_and_drop_preserve_replacement_and_symlink() {
        let (root, _source, output) = fixture();
        let directory = fs::File::open(output.parent().unwrap()).unwrap();
        let name = output_name(&output).unwrap();
        let mut copy = create_output(&directory).unwrap();
        copy.write_all(b"partial").unwrap();
        fs::write(&output, b"replacement").unwrap();
        assert_eq!(
            publish_output(&copy, &directory, &name).unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        drop(copy);
        assert_eq!(fs::read(&output).unwrap(), b"replacement");
        fs::remove_file(&output).unwrap();
        let target = root.join("target");
        fs::write(&target, b"unrelated").unwrap();
        std::os::unix::fs::symlink(&target, &output).unwrap();
        let copy = create_output(&directory).unwrap();
        assert_eq!(
            publish_output(&copy, &directory, &name).unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        drop(copy);
        assert!(
            fs::symlink_metadata(&output)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert_eq!(fs::read(target).unwrap(), b"unrelated");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn dropping_failed_copy_creates_no_directory_entry() {
        let (root, _source, output) = fixture();
        let directory = fs::File::open(output.parent().unwrap()).unwrap();
        let mut copy = create_output(&directory).unwrap();
        copy.write_all(b"partial").unwrap();
        drop(copy);
        assert!(!output.exists());
        assert_eq!(fs::read_dir(output.parent().unwrap()).unwrap().count(), 0);
        fs::remove_dir_all(root).unwrap();
    }
}
