// SPDX-License-Identifier: MPL-2.0
//! Strict local Argon2id verification. No login IPC, password prompt or
//! enrollment command exists yet. No credential ships in the QEMU rootfs.
use argon2::Argon2;
use argon2::password_hash::{PasswordHash, PasswordVerifier};
use std::fs::{self, OpenOptions};
use std::io::Read;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::Path;

// Trusted build-owned account; never accept UID/GID from a login request.
pub const LOCAL_ACCOUNT: &str = "synveil";
pub const ROOT_CREDENTIAL_PATH: &str = "/etc/synveil/auth/credential.phc";
const PHC_PREFIX: &str = "$argon2id$v=19$m=65536,t=3,p=1$";
const MAX_PHC_BYTES: usize = 160;
const MAX_PASSWORD_BYTES: usize = 1024;
const MIN_PASSWORD_BYTES: usize = 8;
const LINUX_O_NOFOLLOW: i32 = 0o400000;
const LINUX_O_NONBLOCK: i32 = 0o4000;

fn valid_phc_format(phc: &str) -> bool {
    if !phc.starts_with(PHC_PREFIX) || phc.len() > MAX_PHC_BYTES {
        return false;
    }
    let Some((salt, tag)) = phc[PHC_PREFIX.len()..].split_once('$') else {
        return false;
    };
    // PHC base64 without padding: 16 salt bytes -> 22 chars,
    // 32 tag bytes -> 43 chars. No unbounded memory/time parameters.
    let is_phc_base64 = |value: &str| {
        value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'+' || c == b'/')
    };
    salt.len() == 22 && tag.len() == 43 && is_phc_base64(salt) && is_phc_base64(tag)
}

fn verify_phc(username: &str, password: &[u8], phc: &str) -> bool {
    if username != LOCAL_ACCOUNT
        || password.len() < MIN_PASSWORD_BYTES
        || password.len() > MAX_PASSWORD_BYTES
        || !valid_phc_format(phc)
    {
        return false;
    }
    let Ok(parsed) = PasswordHash::new(phc) else {
        return false;
    };
    // Parameters are taken from parsed PHC by PasswordVerifier; the
    // exact prefix above caps algorithm, work factor and output length.
    Argon2::default().verify_password(password, &parsed).is_ok()
}

// The caller cannot supply a hash, salt, arbitrary account or destination.
// Root ownership checks intentionally deny regular unprivileged test runs.
pub fn verify_root_credential(username: &str, password: &[u8]) -> bool {
    if username != LOCAL_ACCOUNT {
        return false;
    }
    read_private_record(Path::new(ROOT_CREDENTIAL_PATH), 0)
        .is_some_and(|phc| verify_phc(username, password, &phc))
}

fn read_private_record(path: &Path, required_uid: u32) -> Option<String> {
    let dir = fs::symlink_metadata(path.parent()?).ok()?;
    if !dir.file_type().is_dir()
        || dir.uid() != required_uid
        || dir.permissions().mode() & 0o777 != 0o700
    {
        return None;
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(LINUX_O_NOFOLLOW | LINUX_O_NONBLOCK)
        .open(path)
        .ok()?;
    let meta = file.metadata().ok()?;
    if !meta.file_type().is_file()
        || meta.uid() != required_uid
        || meta.permissions().mode() & 0o777 != 0o600
        || meta.nlink() != 1
    {
        return None;
    }
    let mut bytes = Vec::new();
    file.take((MAX_PHC_BYTES + 2) as u64)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.last() == Some(&b'\n') {
        bytes.pop();
    }
    if bytes.len() > MAX_PHC_BYTES {
        return None;
    }
    String::from_utf8(bytes)
        .ok()
        .filter(|s| valid_phc_format(s))
}

#[cfg(test)]
mod tests {
    use super::*;
    use argon2::password_hash::{PasswordHasher, SaltString};
    use argon2::{Algorithm, Params, Version};
    use std::sync::atomic::{AtomicU64, Ordering};

    static IDS: AtomicU64 = AtomicU64::new(1);

    fn fixture_phc() -> String {
        let salt = SaltString::encode_b64(b"0123456789ABCDEF").unwrap();
        let parameters = Params::new(65536, 3, 1, Some(32)).unwrap();
        Argon2::new(Algorithm::Argon2id, Version::V0x13, parameters)
            .hash_password(b"unit-test-password", &salt)
            .unwrap()
            .to_string()
    }

    fn private_dir() -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "synveil-cred-{}-{}",
            std::process::id(),
            IDS.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        path
    }

    #[test]
    fn exact_parameters_and_correct_password_required() {
        let phc = fixture_phc();
        assert!(verify_phc(LOCAL_ACCOUNT, b"unit-test-password", &phc));
        assert!(!verify_phc(LOCAL_ACCOUNT, b"wrong-password", &phc));
        assert!(!verify_phc("root", b"unit-test-password", &phc));
        assert!(!verify_phc(LOCAL_ACCOUNT, b"", &phc));
        assert!(!verify_phc(
            LOCAL_ACCOUNT,
            &[b'a'; MAX_PASSWORD_BYTES + 1],
            &phc
        ));
    }

    #[test]
    fn malicious_or_unbounded_phc_never_reaches_expensive_verification() {
        let phc = fixture_phc();
        for bad in [
            phc.replace("argon2id", "argon2i"),
            phc.replace("v=19", "v=16"),
            phc.replace("m=65536", "m=1048576"),
            phc.replace("t=3", "t=9999"),
            phc.replace("p=1", "p=32"),
            format!("{phc}\nother"),
            phc.replace("argon2id", "argon2id-extra"),
            phc.replace("$v=19", ""),
        ] {
            assert!(!valid_phc_format(&bad), "accepted untrusted PHC shape");
            assert!(!verify_phc(LOCAL_ACCOUNT, b"unit-test-password", &bad));
        }
    }

    #[test]
    fn missing_or_symlinked_record_never_authorizes() {
        let dir = private_dir();
        let owner = fs::metadata(&dir).unwrap().uid();
        let record = dir.join("record");
        assert!(read_private_record(&record, owner).is_none());
        let target = dir.join("target");
        fs::write(&target, fixture_phc()).unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
        std::os::unix::fs::symlink(&target, &record).unwrap();
        assert!(read_private_record(&record, owner).is_none());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn record_is_owner_only_single_link_under_private_parent() {
        let dir = private_dir();
        let owner = fs::metadata(&dir).unwrap().uid();
        let record = dir.join("record");
        let phc = fixture_phc();
        fs::write(&record, format!("{phc}\n")).unwrap();
        fs::set_permissions(&record, fs::Permissions::from_mode(0o600)).unwrap();
        assert_eq!(
            read_private_record(&record, owner).as_deref(),
            Some(phc.as_str())
        );
        fs::set_permissions(&record, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(read_private_record(&record, owner).is_none());
        fs::set_permissions(&record, fs::Permissions::from_mode(0o600)).unwrap();
        fs::hard_link(&record, dir.join("other")).unwrap();
        assert!(read_private_record(&record, owner).is_none());
        fs::remove_file(dir.join("other")).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o777)).unwrap();
        assert!(read_private_record(&record, owner).is_none());
        fs::remove_dir_all(dir).unwrap();
    }
}
