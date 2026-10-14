// SPDX-License-Identifier: MPL-2.0
//! Owner-controlled offline copies. Checksums do not authenticate event origin.
use blake2::{Blake2b512, Digest};
use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;

const NOFOLLOW_NONBLOCK: i32 = 0o400000 | 0o4000;

unsafe extern "C" {
    fn geteuid() -> u32;
}

fn checksum(bytes: &[u8]) -> String {
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
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(NOFOLLOW_NONBLOCK)
            .open(output)?;
        let created = file.metadata()?;
        let saved = (|| {
            if !created.is_file()
                || created.uid() != meta.uid()
                || created.nlink() != 1
                || created.mode() & 0o777 != 0o600
            {
                return Err(unsafe_output());
            }
            file.write_all(bytes)?;
            file.sync_all()?;
            directory.sync_all()?;
            let now = fs::symlink_metadata(output)?;
            let parent_now = fs::symlink_metadata(parent)?;
            if (now.dev(), now.ino()) != (created.dev(), created.ino())
                || (parent_now.dev(), parent_now.ino()) != (meta.dev(), meta.ino())
            {
                return Err(unsafe_output());
            }
            Ok(receipt(bytes, &inspected, "synveil.audit-export/v1"))
        })();
        if saved.is_err()
            && fs::symlink_metadata(output).is_ok_and(|now| {
                (now.dev(), now.ino()) == (created.dev(), created.ino())
            })
        {
            // Never remove a replacement object after a failed copy.
            let _ = fs::remove_file(output);
        }
        saved
    })
}

fn unsafe_output() -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, "unsafe audit export output")
}

/// Verify exact bytes against a separately retained owner-supplied digest.
pub fn verify(path: &Path, expected: &str) -> io::Result<Value> {
    if expected.len() != 128
        || !expected.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid digest"));
    }
    super::audit_check::with_snapshot(path, |bytes, inspected| {
        let report = receipt(bytes, &inspected, "synveil.audit-verification/v1");
        if report["digest"] != expected {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "digest mismatch"));
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
            "synveil-export-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
        let dest = root.join("copies");
        fs::DirBuilder::new().mode(0o700).create(&dest).unwrap();
        let source = root.join("journal");
        let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&source).unwrap();
        writeln!(file, "{}", super::super::audit::record("request", json!({"private": "secret"}))).unwrap();
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
        let text = fs::read_to_string(&output).unwrap().replace("secret", "edited");
        fs::write(&output, text).unwrap();
        assert!(super::super::audit_check::inspect(&output).is_ok());
        assert_eq!(verify(&output, report["digest"].as_str().unwrap()).unwrap_err().kind(), io::ErrorKind::InvalidData);
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
            assert_eq!(verify(&source, digest).unwrap_err().kind(), io::ErrorKind::InvalidInput);
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn blake2b_digest_matches_published_vector() {
        assert_eq!(checksum(b"hello world"), concat!(
            "021ced8799296ceca557832ab941a50b4a11f83478cf141f51f933f653ab9fbc",
            "c05a037cddbed06e309bf334942c4e58cdf1a46e237911ccd7fcf9787cbc7fd0"
        ));
    }

    #[test]
    fn busy_source_refuses_export_without_creating_output() {
        use std::os::unix::io::AsRawFd;
        unsafe extern "C" {
            fn flock(fd: i32, operation: i32) -> i32;
        }
        let (root, source, output) = fixture();
        let held = super::super::audit::directory_lock(&source, true).unwrap().unwrap();
        assert_eq!(export(&source, &output).unwrap_err().kind(), io::ErrorKind::WouldBlock);
        assert!(!output.exists());
        assert_eq!(unsafe { flock(held.as_raw_fd(), 8) }, 0);
        drop(held);
        export(&source, &output).unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
