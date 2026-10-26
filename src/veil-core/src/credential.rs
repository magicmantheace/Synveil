// SPDX-License-Identifier: MPL-2.0
//! Strict local Argon2id verification. No login IPC, password prompt or
//! login service exists yet. No credential ships in the QEMU rootfs.
use argon2::Argon2;
use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::{Algorithm, Params, Version};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::Path;

// Trusted build-owned account; never accept UID/GID from a login request.
pub const LOCAL_ACCOUNT: &str = "synveil";
pub const ROOT_CREDENTIAL_PATH: &str = "/var/lib/synveil/auth/credential.phc";
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
    if username != LOCAL_ACCOUNT || !has_persistent_state() {
        return false;
    }
    read_root_private_record(Path::new(ROOT_CREDENTIAL_PATH), 0)
        .is_some_and(|phc| verify_phc(username, password, &phc))
}

// Credential authority is bound to the designated state partition, in
// two layers that must both hold:
//
// 1. The mount table must show exactly one /var mount, ext4 and
//    writable. A duplicate or contradictory mount table is ambiguous
//    and fails closed: a generic ext4 mount over /var is NOT an
//    authority to read/write an owner's password verifier, e.g. a
//    rebound home or an unexpected writable volume must fail closed.
// 2. The mounted device must be the partition carrying the project's
//    fixed GPT identity (partition name and PARTUUID below), resolved
//    through sysfs, never through a device name. The same policy
//    covers the state partition inside the install image and a
//    separately attached partitioned state disk, on QEMU and on
//    physical hardware alike. There is no fallback to any other device.
//
// The GUID is a role label, not a secret: whoever controls disk
// attachment already controls the machine. Authenticity of install
// media belongs to the signed installer milestone. Keep the GUID in
// sync with tools/state_disk.py, build/rootfs/mount-state, and
// src/veil-display/enroll.c.
pub const STATE_PARTUUID: &str = "7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053";
pub const STATE_PARTNAME: &str = "Synveil State";

fn state_mount_device(mounts: &str) -> Option<String> {
    let mut matching = 0u8;
    let mut found = None;
    for line in mounts.lines() {
        let mut fields = line.split_whitespace();
        let Some(device) = fields.next() else {
            continue;
        };
        let Some(target) = fields.next() else {
            continue;
        };
        if target != "/var" {
            continue;
        }
        matching = matching.checked_add(1)?;
        let fs_type = fields.next();
        let mount_flags = fields.next().unwrap_or("");
        let writable = mount_flags.split(',').any(|flag| flag == "rw");
        if fs_type != Some("ext4") || !writable {
            return None;
        }
        found = Some(device.to_string());
    }
    if matching == 1 { found } else { None }
}

fn uevent_is_state_partition(uevent: &str) -> bool {
    let mut partuuid = None;
    let mut partname = None;
    for line in uevent.lines() {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let slot = match key {
            "PARTUUID" => &mut partuuid,
            "PARTNAME" => &mut partname,
            _ => continue,
        };
        // A repeated identity key is ambiguous even when it repeats the
        // expected value; the kernel emits each key at most once.
        if slot.is_some() {
            return false;
        }
        *slot = Some(value);
    }
    partuuid == Some(STATE_PARTUUID) && partname == Some(STATE_PARTNAME)
}

fn device_is_state_partition(device: &str) -> bool {
    let Ok(metadata) = fs::metadata(device) else {
        return false;
    };
    if !metadata.file_type().is_block_device() {
        return false;
    }
    let rdev = metadata.rdev();
    // dev_t encoding (new_encode_dev): the minor's low 8 bits and the
    // major's low 12 bits sit at bit 0 and bit 8; the remaining bits of
    // each sit above bit 12 and bit 32 respectively.
    let major = ((rdev >> 8) & 0xfff) | ((rdev >> 32) & 0xffff_f000);
    let minor = (rdev & 0xff) | ((rdev >> 12) & 0xffff_ff00);
    let Ok(uevent) = fs::read_to_string(format!("/sys/dev/block/{major}:{minor}/uevent")) else {
        return false;
    };
    uevent_is_state_partition(&uevent)
}

fn has_persistent_state() -> bool {
    let Ok(mounts) = fs::read_to_string("/proc/mounts") else {
        return false;
    };
    let Some(device) = state_mount_device(&mounts) else {
        return false;
    };
    device_is_state_partition(&device)
}

/// One-time developer-VM enrollment; read password from an already protected
/// stream, never from arguments or environment. No automatic enrollment.
/// The graphical UI and AI must not call this privileged routine.
pub fn enroll_root_credential(password: &[u8]) -> io::Result<()> {
    if !has_persistent_state() {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "persistent state unavailable",
        ));
    }
    let record = Path::new(ROOT_CREDENTIAL_PATH);
    let base = record
        .parent()
        .and_then(Path::parent)
        .ok_or_else(unsafe_path)?;
    enroll_in_private_state(base, 0, password)
}

fn unsafe_path() -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        "untrusted credential storage",
    )
}

fn private_directory(dir: &Path, uid: u32) -> bool {
    fs::symlink_metadata(dir).is_ok_and(|meta| {
        meta.file_type().is_dir() && meta.uid() == uid && meta.permissions().mode() & 0o777 == 0o700
    })
}

fn enroll_in_private_state(base: &Path, uid: u32, password: &[u8]) -> io::Result<()> {
    if password.len() < MIN_PASSWORD_BYTES
        || password.len() > MAX_PASSWORD_BYTES
        || !private_directory(base, uid)
    {
        return Err(unsafe_path());
    }
    let auth = base.join("auth");
    if !auth.exists() && !auth.is_symlink() {
        fs::DirBuilder::new().mode(0o700).create(&auth)?;
    }
    if !private_directory(&auth, uid) {
        return Err(unsafe_path());
    }
    let path = auth.join("credential.phc");
    // Refuse existing credentials without replacement, even if symlinked.
    if fs::symlink_metadata(&path).is_ok() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "already enrolled",
        ));
    }
    let mut salt_bytes = [0u8; 16];
    fs::File::open("/dev/urandom")?.read_exact(&mut salt_bytes)?;
    let salt = SaltString::encode_b64(&salt_bytes).map_err(|_| unsafe_path())?;
    let parameters = Params::new(65536, 3, 1, Some(32)).map_err(|_| unsafe_path())?;
    let phc = Argon2::new(Algorithm::Argon2id, Version::V0x13, parameters)
        .hash_password(password, &salt)
        .map_err(|_| unsafe_path())?
        .to_string();
    if !valid_phc_format(&phc) {
        return Err(unsafe_path());
    }
    let mut out = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(LINUX_O_NOFOLLOW | LINUX_O_NONBLOCK)
        .open(&path)?;
    let result = (|| {
        out.write_all(phc.as_bytes())?;
        out.write_all(b"\n")?;
        out.sync_all()?;
        fs::File::open(&auth)?.sync_all()
    })();
    if result.is_err() {
        let _ = fs::remove_file(&path);
    }
    result
}

// Both the credential directory and its state ancestor must be root-private.
// Otherwise a relaxed or substituted base could allow an untrusted record
// to authorize login even if the immediate auth directory stays mode 0700.
fn read_root_private_record(path: &Path, required_uid: u32) -> Option<String> {
    if !private_directory(path.parent()?.parent()?, required_uid) {
        return None;
    }
    read_private_record(path, required_uid)
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

    #[test]
    fn only_unique_writable_var_mount_names_the_credential_device() {
        let expected =
            "/dev/vda / ext4 rw,relatime 0 0\n/dev/vdb1 /var ext4 rw,nosuid,nodev,noatime 0 0\n";
        assert_eq!(state_mount_device(expected).as_deref(), Some("/dev/vdb1"));
        // The device name itself is not the authority at this layer: any
        // name is returned and must still pass the GPT identity check
        // before a credential is touched.
        assert_eq!(
            state_mount_device("/dev/sda2 /var ext4 rw 0 0\n").as_deref(),
            Some("/dev/sda2")
        );
        for wrong in [
            "",
            "/dev/vdb1 /var ext4 ro,nosuid,nodev 0 0\n",
            "tmpfs /var tmpfs rw 0 0\n",
            "/dev/vdb1 /var xfs rw 0 0\n",
            "/dev/vdb1 /var ext4 defaults 0 0\n",
            "/dev/vdb1 /var ext4\n",
            "/dev/vdb1 /var ext4 rw 0 0\n/dev/vdb1 /var ext4 rw 0 0\n",
            "/dev/vdb1 /var ext4 rw 0 0\n/dev/vda2 /var ext4 ro 0 0\n",
            "/dev/vdb /var\\040backup ext4 rw 0 0\n",
        ] {
            assert_eq!(
                state_mount_device(wrong),
                None,
                "unsafe mount accepted: {wrong}"
            );
        }
        assert_eq!(
            state_mount_device(
                "/dev/vdb1 /var ext4 rw,nosuid,nodev 0 0\n/dev/vdc /home ext4 rw 0 0\n"
            )
            .as_deref(),
            Some("/dev/vdb1")
        );
    }

    #[test]
    fn only_designated_gpt_identity_is_the_state_partition() {
        let expected = "MAJOR=254\nMINOR=17\nDEVNAME=vdb1\nDEVTYPE=partition\n\
                        PARTN=1\nPARTNAME=Synveil State\n\
                        PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n";
        assert!(uevent_is_state_partition(expected));
        for wrong in [
            "",
            // A whole disk has no partition identity at all.
            "MAJOR=254\nMINOR=16\nDEVNAME=vdb\nDEVTYPE=disk\n",
            // Another project's partition.
            "DEVNAME=sda2\nDEVTYPE=partition\nPARTNAME=Synveil State\n\
             PARTUUID=0c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n",
            "DEVNAME=sda2\nDEVTYPE=partition\nPARTNAME=Other State\n\
             PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n",
            "DEVNAME=sda2\nDEVTYPE=partition\n\
             PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n",
            // A repeated identity key is ambiguous, even a faithful one.
            "DEVNAME=sda2\nDEVTYPE=partition\nPARTNAME=Synveil State\n\
             PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n\
             PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n",
        ] {
            assert!(
                !uevent_is_state_partition(wrong),
                "unsafe uevent accepted: {wrong}"
            );
        }
    }

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
    fn one_time_enrollment_is_private_and_refuses_replacement() {
        let dir = private_dir();
        let owner = fs::metadata(&dir).unwrap().uid();
        enroll_in_private_state(&dir, owner, b"fixture-enrollment-password").unwrap();
        let path = dir.join("auth/credential.phc");
        let record = read_root_private_record(&path, owner).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(read_root_private_record(&path, owner).is_none());
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        assert!(verify_phc(
            LOCAL_ACCOUNT,
            b"fixture-enrollment-password",
            &record
        ));
        assert_eq!(fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
        assert_eq!(
            fs::metadata(dir.join("auth")).unwrap().mode() & 0o777,
            0o700
        );
        let before = fs::read(&path).unwrap();
        assert_eq!(
            enroll_in_private_state(&dir, owner, b"another-fixture-password")
                .unwrap_err()
                .kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(fs::read(&path).unwrap(), before);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn enrollment_rejects_suspicious_auth_parent_and_secret_sizes() {
        let dir = private_dir();
        let owner = fs::metadata(&dir).unwrap().uid();
        let other = private_dir();
        std::os::unix::fs::symlink(&other, dir.join("auth")).unwrap();
        assert_eq!(
            enroll_in_private_state(&dir, owner, b"valid-test-password")
                .unwrap_err()
                .kind(),
            io::ErrorKind::PermissionDenied
        );
        fs::remove_file(dir.join("auth")).unwrap();
        assert_eq!(
            enroll_in_private_state(&dir, owner, b"short")
                .unwrap_err()
                .kind(),
            io::ErrorKind::PermissionDenied
        );
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o777)).unwrap();
        assert_eq!(
            enroll_in_private_state(&dir, owner, b"valid-test-password")
                .unwrap_err()
                .kind(),
            io::ErrorKind::PermissionDenied
        );
        fs::remove_dir_all(dir).unwrap();
        fs::remove_dir_all(other).unwrap();
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
