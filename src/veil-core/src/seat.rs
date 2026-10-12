// SPDX-License-Identifier: MPL-2.0
//! Developer VM's root-owned graphical seat authority. This is intentionally
//! distinct from veil-core's read-only status/observe socket.
//!
//! Protocol v1: four bytes "SVL1", two-byte BE password length (8..1024),
//! followed by password bytes. Username/UID/GID/command are NOT client fields.
//! One connection holds the entire session lease. Losing that connection
//! kills the fixed launcher; there is no token that can be replayed.
//! The current developer framebuffer has NOT yet been wired to this service.
use crate::session::{SessionError, SessionState, SessionTracker};
use std::fs::{self, Permissions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::os::unix::io::AsRawFd;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::process::{Child, Command};
use std::time::Duration;

pub const DEFAULT_SEAT_SOCKET: &str = "/run/synveil/veil-seat.sock";
const HEADER_MAGIC: &[u8; 4] = b"SVL1";
const MAX_SECRET: usize = 1024;
const MIN_SECRET: usize = 8;
const IO_TIMEOUT: Duration = Duration::from_secs(4);
const LEASE_POLL: Duration = Duration::from_millis(100);

// Linux SO_PEERCRED. Never trust a claimed account/UID inside the frame.
#[repr(C)]
#[derive(Clone, Copy)]
struct LinuxUcred {
    pid: i32,
    uid: u32,
    gid: u32,
}

unsafe extern "C" {
    fn getsockopt(
        socket: i32,
        level: i32,
        option: i32,
        value: *mut LinuxUcred,
        length: *mut u32,
    ) -> i32;
}

fn peer_uid(stream: &UnixStream) -> io::Result<u32> {
    let mut cred = LinuxUcred {
        pid: 0,
        uid: u32::MAX,
        gid: u32::MAX,
    };
    let mut len = u32::try_from(std::mem::size_of::<LinuxUcred>())
        .map_err(|_| io::Error::other("invalid Linux ucred size"))?;
    // SOL_SOCKET=1, SO_PEERCRED=17 on the pinned Linux x86_64 target.
    let ok = unsafe { getsockopt(stream.as_raw_fd(), 1, 17, &mut cred, &mut len) };
    if ok != 0 {
        return Err(io::Error::last_os_error());
    }
    if len as usize != std::mem::size_of::<LinuxUcred>() || cred.pid <= 0 {
        return Err(io::Error::other("unexpected Unix peer credentials"));
    }
    Ok(cred.uid)
}

fn read_secret(stream: &mut UnixStream) -> io::Result<Vec<u8>> {
    stream.set_read_timeout(Some(IO_TIMEOUT))?;
    let mut header = [0u8; 6];
    stream.read_exact(&mut header)?;
    let length = u16::from_be_bytes([header[4], header[5]]) as usize;
    if &header[..4] != HEADER_MAGIC || !(MIN_SECRET..=MAX_SECRET).contains(&length) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid seat frame",
        ));
    }
    let mut secret = vec![0u8; length];
    if let Err(error) = stream.read_exact(&mut secret) {
        secret.fill(0);
        return Err(error);
    }
    Ok(secret)
}

fn prepare_seat_socket(path: &Path, owner: u32) -> io::Result<UnixListener> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("seat socket missing parent"))?;
    let meta = fs::symlink_metadata(parent)?;
    if !meta.file_type().is_dir()
        || meta.uid() != owner
        || meta.permissions().mode() & 0o777 != 0o700
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "unsafe seat directory",
        ));
    }
    if fs::symlink_metadata(path).is_ok() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "seat socket exists",
        ));
    }
    let listener = UnixListener::bind(path)?;
    fs::set_permissions(path, Permissions::from_mode(0o600))?;
    Ok(listener)
}

fn start_fixed_terminal() -> io::Result<Child> {
    // No shell interpolation or client-selected argv. synveil-vt forks an
    // unprivileged UID 1000 shell and waits for it, with parent-death guards.
    Command::new("/usr/bin/synveil-vt")
        .arg("--demo-terminal")
        .spawn()
}

fn close_session(tracker: &mut SessionTracker, session: u64, child: &mut Child) {
    // Never grant another login until this process has been reaped.
    tracker.display_failed();
    let _ = child.kill();
    // Never release the cleanup quarantine unless the launcher has
    // definitely been reaped (which triggers its non-root PDEATHSIG).
    if child.wait().is_ok() {
        let _ = tracker.cleanup_finished(session);
    }
}

/// Root graphical clients only. Kept separate from the agent/control IPC.
/// Exactly one authenticated client owns the seat and a live socket lease.
fn handle_connection<F, L>(
    stream: &mut UnixStream,
    tracker: &mut SessionTracker,
    required_uid: u32,
    verify: F,
    launch: L,
) -> io::Result<()>
where
    F: FnOnce(&[u8]) -> bool,
    L: FnOnce() -> io::Result<Child>,
{
    if peer_uid(stream)? != required_uid {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "peer not authorized",
        ));
    }
    let mut secret = match read_secret(stream) {
        Ok(value) => value,
        Err(_) => {
            let _ = stream.write_all(b"DENIED\n");
            return Ok(());
        }
    };
    let attempt = match tracker.begin_login() {
        Ok(value) => value,
        Err(SessionError::Throttled) => {
            secret.fill(0);
            let _ = stream.write_all(b"THROTTLED\n");
            return Ok(());
        }
        Err(_) => {
            secret.fill(0);
            let _ = stream.write_all(b"DENIED\n");
            return Ok(());
        }
    };
    let verified = tracker.complete_with_verifier(
        attempt,
        crate::credential::LOCAL_ACCOUNT,
        &secret,
        |_, value| verify(value),
    );
    secret.fill(0);
    if verified.is_err() {
        let _ = stream.write_all(b"DENIED\n");
        return Ok(());
    }
    let mut child = match launch() {
        Ok(child) => child,
        Err(_) => {
            tracker.display_failed();
            let _ = tracker.cleanup_finished(attempt);
            let _ = stream.write_all(b"DENIED\n");
            return Ok(());
        }
    };
    if stream.write_all(b"ACTIVE\n").is_err() {
        close_session(tracker, attempt, &mut child);
        return Ok(());
    }

    // Persistent socket = lease. Any input/EOF, a dead client, terminal
    // exit or I/O failure revokes it. The UI cannot retain authority after
    // closing the connection or crashing.
    stream.set_read_timeout(Some(LEASE_POLL))?;
    let mut signal = [0u8; 8];
    loop {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => break,
            Ok(None) => {}
        }
        match stream.read(&mut signal) {
            Ok(0) | Ok(_) => break,
            Err(error)
                if error.kind() == io::ErrorKind::TimedOut
                    || error.kind() == io::ErrorKind::WouldBlock => {}
            Err(_) => break,
        }
    }
    close_session(tracker, attempt, &mut child);
    let _ = stream.write_all(b"LOCKED\n");
    Ok(())
}

/// Developer-only password-pipe client. The socket lease stays open until
/// the non-root VT shell terminates. The client never receives a UID, an
/// executable name, a session token or a privileged command capability.
pub fn login_from_reader<R: Read>(input: R, socket: &Path) -> io::Result<bool> {
    let mut password = Vec::new();
    if let Err(error) = input
        .take((MAX_SECRET + 1) as u64)
        .read_to_end(&mut password)
    {
        password.fill(0);
        return Err(error);
    }
    if !(MIN_SECRET..=MAX_SECRET).contains(&password.len()) {
        password.fill(0);
        return Ok(false);
    }
    let mut stream = match UnixStream::connect(socket) {
        Ok(stream) => stream,
        Err(error) => {
            password.fill(0);
            return Err(error);
        }
    };
    let write = (|| {
        stream.set_write_timeout(Some(IO_TIMEOUT))?;
        let len = u16::try_from(password.len())
            .map_err(|_| io::Error::other("invalid password size"))?;
        stream.write_all(HEADER_MAGIC)?;
        stream.write_all(&len.to_be_bytes())?;
        stream.write_all(&password)
    })();
    password.fill(0);
    write?;
    stream.set_read_timeout(Some(IO_TIMEOUT))?;
    let mut response = Vec::new();
    let mut byte = [0u8; 1];
    while response.len() <= 16 {
        if stream.read_exact(&mut byte).is_err() {
            return Ok(false);
        }
        response.push(byte[0]);
        if byte[0] == b'\n' {
            break;
        }
    }
    if response != b"ACTIVE\n" {
        return Ok(false);
    }
    println!("SYNVEIL_SEAT_ACTIVE");
    // Preserve the lease. A client crash closes the FD and causes the root
    // broker to revoke the child session.
    stream.set_read_timeout(None)?;
    loop {
        match stream.read(&mut byte) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
    }
    Ok(true)
}

pub fn serve(path: &Path) -> io::Result<()> {
    if unsafe { native_euid() } != 0 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "root seat required",
        ));
    }
    let listener = prepare_seat_socket(path, 0)?;
    let mut tracker = SessionTracker::default();
    println!("SYNVEIL_SEAT_READY");
    for accepted in listener.incoming() {
        match accepted {
            Ok(mut stream) => {
                // No password bytes, hashes, or credential-dependent errors
                // are ever logged by this process.
                let _ = handle_connection(
                    &mut stream,
                    &mut tracker,
                    0,
                    |password| {
                        crate::credential::verify_root_credential(
                            crate::credential::LOCAL_ACCOUNT,
                            password,
                        )
                    },
                    start_fixed_terminal,
                );
            }
            Err(_) => continue,
        }
        // If the launcher did not complete cleanup, remain quarantined.
        if matches!(tracker.state(), SessionState::CleaningUp { .. }) {
            return Err(io::Error::other("seat cleanup incomplete"));
        }
    }
    Ok(())
}

unsafe extern "C" {
    #[link_name = "geteuid"]
    fn native_euid() -> u32;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    fn send_login(stream: &mut UnixStream, password: &[u8]) {
        stream.write_all(HEADER_MAGIC).unwrap();
        stream
            .write_all(&(password.len() as u16).to_be_bytes())
            .unwrap();
        stream.write_all(password).unwrap();
    }

    #[test]
    fn peer_identity_comes_from_kernel() {
        let (stream, _) = UnixStream::pair().unwrap();
        assert_eq!(peer_uid(&stream).unwrap(), unsafe { native_euid() });
    }

    #[test]
    fn refused_password_stays_locked_and_never_launches() {
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let owner = unsafe { native_euid() };
        let worker = thread::spawn(move || {
            let mut tracker = SessionTracker::default();
            handle_connection(
                &mut server,
                &mut tracker,
                owner,
                |_| false,
                || panic!("denial must not launch"),
            )
            .unwrap();
            tracker.state()
        });
        send_login(&mut client, b"incorrect-password");
        let mut response = [0u8; 7];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"DENIED\n");
        assert_eq!(worker.join().unwrap(), SessionState::Locked);
    }

    #[test]
    fn correct_password_launches_fixed_worker_and_disconnect_cleans() {
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let owner = unsafe { native_euid() };
        let worker = thread::spawn(move || {
            let mut tracker = SessionTracker::default();
            handle_connection(
                &mut server,
                &mut tracker,
                owner,
                |password| password == b"correct-password",
                || Command::new("sleep").arg("30").spawn(),
            )
            .unwrap();
            tracker.state()
        });
        send_login(&mut client, b"correct-password");
        let mut response = [0u8; 7];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"ACTIVE\n");
        drop(client);
        assert_eq!(worker.join().unwrap(), SessionState::Locked);
    }

    #[test]
    fn oversized_and_truncated_frames_cannot_launch() {
        let owner = unsafe { native_euid() };
        for raw in [
            b"SVL1\x04\x01".as_slice(),
            b"BAD!\x00\x10".as_slice(),
            b"SVL1\x00\x10tiny".as_slice(),
        ] {
            let (mut client, mut server) = UnixStream::pair().unwrap();
            let worker = thread::spawn(move || {
                let mut tracker = SessionTracker::default();
                handle_connection(
                    &mut server,
                    &mut tracker,
                    owner,
                    |_| panic!("invalid frame verified"),
                    || panic!("invalid frame launched"),
                )
                .unwrap();
                tracker.state()
            });
            client.write_all(raw).unwrap();
            client.shutdown(std::net::Shutdown::Write).unwrap();
            let mut response = String::new();
            client.read_to_string(&mut response).unwrap();
            assert_eq!(response, "DENIED\n");
            assert_eq!(worker.join().unwrap(), SessionState::Locked);
        }
    }

    #[test]
    fn seat_socket_refuses_symlinks_and_world_writable_parents() {
        let dir = std::env::temp_dir().join(format!("synveil-seat-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, Permissions::from_mode(0o700)).unwrap();
        let owner = fs::metadata(&dir).unwrap().uid();
        let socket = dir.join("seat.sock");
        let listener = prepare_seat_socket(&socket, owner).unwrap();
        assert_eq!(
            fs::metadata(&socket).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert!(prepare_seat_socket(&socket, owner).is_err());
        drop(listener);
        fs::remove_file(&socket).unwrap();
        std::os::unix::fs::symlink("missing", &socket).unwrap();
        assert!(prepare_seat_socket(&socket, owner).is_err());
        fs::remove_file(&socket).unwrap();
        fs::set_permissions(&dir, Permissions::from_mode(0o777)).unwrap();
        assert!(prepare_seat_socket(&socket, owner).is_err());
        fs::remove_dir_all(&dir).unwrap();
    }
}
