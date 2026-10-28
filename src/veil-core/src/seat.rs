// SPDX-License-Identifier: MPL-2.0
//! Developer VM's root-owned graphical seat authority. This is intentionally
//! distinct from veil-core's read-only status/observe socket.
//!
//! Protocol v1: four bytes "SVL1", two-byte BE password length (8..1024),
//! followed by password bytes. Username/UID/GID/command are NOT client fields.
//! One connection holds the entire session lease. Losing that connection
//! kills the fixed launcher; there is no token that can be replayed.
//! The opt-in developer graphical login is wired to this lease boundary.
use crate::session::{SessionError, SessionState, SessionTracker};
use std::fs::{self, File, Permissions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::io::AsRawFd;
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command};
use std::time::{Duration, Instant};

pub const DEFAULT_SEAT_SOCKET: &str = "/run/synveil/veil-seat.sock";
const HEADER_MAGIC: &[u8; 4] = b"SVL1";
const MAX_SECRET: usize = 1024;
const MIN_SECRET: usize = 8;
const IO_TIMEOUT: Duration = Duration::from_secs(4);
const LEASE_POLL: Duration = Duration::from_millis(100);
const VT_ACTIVATION_GRACE: Duration = Duration::from_secs(5);
// synveil-vt activates tty1; the graphical password client is on tty2.
const AUTHENTICATED_TERMINAL_VT: u16 = 1;
// VT_GETSTATE is Linux UAPI ioctl 0x5603 on our pinned x86_64 kernel.
const VT_GETSTATE: u64 = 0x5603;

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct LinuxVtState {
    active: u16,
    _signal: u16,
    _state: u16,
}

// A newly launched terminal has a short chance to activate tty1. After
// tty1 was seen, switching to any other VT revokes the entire session.
// This prevents Ctrl+Alt+Fn from hiding a still-unlocked console behind
// the graphical login; startup failure also fails closed.
struct VtLease {
    launched: Instant,
    activated: bool,
}

impl VtLease {
    fn new() -> Self {
        Self {
            launched: Instant::now(),
            activated: false,
        }
    }

    fn permits(&mut self, active: u16, now: Instant) -> bool {
        if active == AUTHENTICATED_TERMINAL_VT {
            self.activated = true;
            return true;
        }
        !self.activated && now.duration_since(self.launched) < VT_ACTIVATION_GRACE
    }
}

fn active_virtual_terminal() -> io::Result<u16> {
    let tty = File::open("/dev/tty0")?;
    let mut state = LinuxVtState::default();
    let rc = unsafe { ioctl(tty.as_raw_fd(), VT_GETSTATE, &mut state) };
    if rc < 0 {
        return Err(io::Error::last_os_error());
    }
    if state.active == 0 {
        return Err(io::Error::other("no active virtual terminal"));
    }
    Ok(state.active)
}

// Linux SO_PEERCRED. Never trust a claimed account/UID inside the frame.
#[repr(C)]
#[derive(Clone, Copy)]
struct LinuxUcred {
    pid: i32,
    uid: u32,
    gid: u32,
}

unsafe extern "C" {
    fn getpid() -> i32;
    fn getppid() -> i32;
    fn prctl(option: i32, ...) -> i32;
    fn ioctl(fd: i32, request: u64, ...) -> i32;
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

// A socket read timeout alone is insufficient: it resets on every recv,
// allowing a peer to hold the single-threaded broker with one byte at a time.
// All header and password bytes share a *single* monotonic deadline.
fn read_exact_before(
    stream: &mut UnixStream,
    buffer: &mut [u8],
    deadline: Instant,
) -> io::Result<()> {
    let mut received = 0;
    while received < buffer.len() {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "seat frame deadline",
            ));
        }
        stream.set_read_timeout(Some(remaining))?;
        match stream.read(&mut buffer[received..]) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "seat frame closed",
                ));
            }
            Ok(size) => received += size,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn read_secret_until(stream: &mut UnixStream, deadline: Instant) -> io::Result<Vec<u8>> {
    let mut header = [0u8; 6];
    read_exact_before(stream, &mut header, deadline)?;
    let length = u16::from_be_bytes([header[4], header[5]]) as usize;
    if &header[..4] != HEADER_MAGIC || !(MIN_SECRET..=MAX_SECRET).contains(&length) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid seat frame",
        ));
    }
    let mut secret = vec![0u8; length];
    if let Err(error) = read_exact_before(stream, &mut secret, deadline) {
        crate::secret::clear(&mut secret);
        return Err(error);
    }
    Ok(secret)
}

fn read_secret(stream: &mut UnixStream) -> io::Result<Vec<u8>> {
    read_secret_until(stream, Instant::now() + IO_TIMEOUT)
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
    match fs::symlink_metadata(path) {
        Ok(meta)
            if meta.file_type().is_socket()
                && meta.uid() == owner
                && meta.permissions().mode() & 0o777 == 0o600
                && meta.nlink() == 1 =>
        {
            // Recover only from a refused, private, singly linked stale
            // listener. Active sockets and ambiguous errors are left intact.
            // The parent is mode 0700, so a non-root process cannot replace
            // this path between the identity check and socket removal.
            match UnixStream::connect(path) {
                Ok(_) => {
                    return Err(io::Error::new(
                        io::ErrorKind::AlreadyExists,
                        "seat already active",
                    ));
                }
                Err(error) if error.kind() == io::ErrorKind::ConnectionRefused => {
                    fs::remove_file(path)?;
                }
                Err(error) => return Err(error),
            }
        }
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "refusing unsafe existing seat endpoint",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let listener = UnixListener::bind(path)?;
    fs::set_permissions(path, Permissions::from_mode(0o600))?;
    Ok(listener)
}

/// Linux protects the entire nested VT process chain if the seat authority
/// crashes: once its immediate launcher dies, synveil-vt's own parent-death
/// guard reaps the non-root terminal. pre_exec is intentionally async-signal
/// safe; no heap allocation, logging or locks between fork and exec.
fn spawn_guarded(mut command: Command) -> io::Result<Child> {
    let expected = unsafe { getpid() };
    unsafe {
        command.pre_exec(move || {
            // PR_SET_PDEATHSIG=1 and SIGKILL=9 are stable Linux UAPI values.
            if prctl(1, 9u64, 0u64, 0u64, 0u64) != 0 {
                return Err(io::Error::last_os_error());
            }
            if getppid() != expected {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "seat broker exited during terminal launch",
                ));
            }
            Ok(())
        });
    }
    command.spawn()
}

const WESTON_SESSION_MARKER: &str = "/etc/synveil/weston-session";

/// The joined graphical profile selects the Weston session launcher
/// through an immutable rootfs marker, validated exactly like the
/// display profile markers: a regular file, never a symlink, root-owned,
/// mode 0644. An absent marker selects the framebuffer terminal; a
/// marker that fails validation refuses the launch entirely rather
/// than silently falling back to a different session type.
fn weston_marker_state(metadata: &fs::Metadata) -> bool {
    !metadata.file_type().is_symlink()
        && metadata.file_type().is_file()
        && metadata.uid() == 0
        && metadata.permissions().mode() & 0o777 == 0o644
}

fn weston_session_mode() -> io::Result<bool> {
    match fs::symlink_metadata(WESTON_SESSION_MARKER) {
        Ok(metadata) => {
            if weston_marker_state(&metadata) {
                Ok(true)
            } else {
                Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "unsafe Weston session marker",
                ))
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

fn start_fixed_terminal() -> io::Result<Child> {
    // No shell interpolation or client-selected argv. This process dies
    // with its broker; its UID1000 child dies with its parent.
    let mut command = Command::new("/usr/bin/synveil-vt");
    command.arg(if weston_session_mode()? {
        "--authenticated-weston"
    } else {
        "--authenticated-terminal"
    });
    spawn_guarded(command)
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
fn handle_connection<F, L, V>(
    stream: &mut UnixStream,
    tracker: &mut SessionTracker,
    required_uid: u32,
    verify: F,
    launch: L,
    mut active_vt: V,
) -> io::Result<()>
where
    F: FnOnce(&[u8]) -> bool,
    L: FnOnce() -> io::Result<Child>,
    V: FnMut() -> io::Result<u16>,
{
    if peer_uid(stream)? != required_uid {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "peer not authorized",
        ));
    }
    // Apply the shared seat's denial cooldown *before* accepting any frame.
    // Otherwise throttled clients can still stall the broker in read_secret.
    let attempt = match tracker.begin_login() {
        Ok(value) => value,
        Err(SessionError::Throttled) => {
            let _ = stream.write_all(b"THROTTLED\n");
            return Ok(());
        }
        Err(_) => {
            let _ = stream.write_all(b"DENIED\n");
            return Ok(());
        }
    };
    let mut secret = match read_secret(stream) {
        Ok(value) => value,
        Err(_) => {
            let _ = tracker.deny_login(attempt);
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
    crate::secret::clear(&mut secret);
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
    if let Err(error) = stream.set_read_timeout(Some(LEASE_POLL)) {
        // A post-launch socket error must never return while the session is
        // active: kill/reap first, then propagate the non-secret error.
        close_session(tracker, attempt, &mut child);
        return Err(error);
    }
    let mut signal = [0u8; 8];
    let mut vt_lease = VtLease::new();
    loop {
        // VT switching is not a lock operation by itself. If the owner
        // switches away from tty1, destroy the terminal and return to the
        // graphical login before any second login can be accepted.
        match active_vt() {
            Ok(vt) if vt_lease.permits(vt, Instant::now()) => {}
            _ => break, // ioctl failure and startup timeout both fail closed.
        }
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
        crate::secret::clear(&mut password);
        return Err(error);
    }
    if !(MIN_SECRET..=MAX_SECRET).contains(&password.len()) {
        crate::secret::clear(&mut password);
        return Ok(false);
    }
    let mut stream = match UnixStream::connect(socket) {
        Ok(stream) => stream,
        Err(error) => {
            crate::secret::clear(&mut password);
            return Err(error);
        }
    };
    let write = (|| {
        stream.set_write_timeout(Some(IO_TIMEOUT))?;
        let len =
            u16::try_from(password.len()).map_err(|_| io::Error::other("invalid password size"))?;
        stream.write_all(HEADER_MAGIC)?;
        stream.write_all(&len.to_be_bytes())?;
        stream.write_all(&password)
    })();
    crate::secret::clear(&mut password);
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
    // Serial login/recovery may already be showing a prompt without newline.
    println!("\nSYNVEIL_SEAT_READY");
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
                    active_virtual_terminal,
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
    fn guarded_launcher_can_start_and_be_reaped() {
        let mut command = Command::new("sh");
        command.arg("-c").arg("exit 0");
        let mut child = spawn_guarded(command).unwrap();
        assert!(child.wait().unwrap().success());
    }

    #[test]
    fn peer_identity_comes_from_kernel() {
        let (stream, _) = UnixStream::pair().unwrap();
        assert_eq!(peer_uid(&stream).unwrap(), unsafe { native_euid() });
    }

    #[test]
    fn weston_marker_must_be_root_owned_regular_0644() {
        use std::os::unix::fs::PermissionsExt;
        let owner = unsafe { native_euid() };
        // The seat runs as root in the guest; on a host test run the
        // "root-owned" expectation is the test runner's own uid, so the
        // positive case is only meaningful when both coincide. The
        // negative cases below hold regardless of who runs the test.
        let dir = std::env::temp_dir().join(format!("synveil-seat-marker-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir(&dir).unwrap();
        let marker = dir.join("weston-session");
        fs::write(&marker, []).unwrap();
        fs::set_permissions(&marker, Permissions::from_mode(0o644)).unwrap();
        let metadata = fs::symlink_metadata(&marker).unwrap();
        assert_eq!(weston_marker_state(&metadata), owner == 0);
        for mode in [0o664, 0o666, 0o600, 0o444] {
            fs::set_permissions(&marker, Permissions::from_mode(mode)).unwrap();
            assert!(
                !weston_marker_state(&fs::symlink_metadata(&marker).unwrap()),
                "mode {mode:o} accepted"
            );
        }
        let link = dir.join("weston-session-link");
        std::os::unix::fs::symlink(&marker, &link).unwrap();
        assert!(!weston_marker_state(&fs::symlink_metadata(&link).unwrap()));
        let _ = fs::remove_dir_all(&dir);
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
                || Ok(AUTHENTICATED_TERMINAL_VT),
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
                || Ok(AUTHENTICATED_TERMINAL_VT),
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
    fn trickled_frame_cannot_extend_the_total_read_deadline() {
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let writer = thread::spawn(move || {
            let frame = b"SVL1\x00\x08password";
            for byte in frame {
                thread::sleep(Duration::from_millis(20));
                if client.write_all(&[*byte]).is_err() {
                    break;
                }
            }
        });
        let deadline = Instant::now() + Duration::from_millis(65);
        let failure = read_secret_until(&mut server, deadline).unwrap_err();
        assert!(matches!(
            failure.kind(),
            io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
        ));
        writer.join().unwrap();
    }

    #[test]
    fn throttled_peer_is_rejected_before_waiting_for_any_password_bytes() {
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let owner = unsafe { native_euid() };
        let mut tracker = SessionTracker::default();
        for _ in 0..3 {
            let attempt = tracker.begin_login().unwrap();
            assert_eq!(
                tracker.complete_with_verifier(
                    attempt,
                    crate::credential::LOCAL_ACCOUNT,
                    b"wrong-password",
                    |_, _| false,
                ),
                Err(SessionError::Denied)
            );
        }
        // An idle client never submits the first byte. Rejection must be
        // immediate, not wait for the four-second read timeout.
        handle_connection(
            &mut server,
            &mut tracker,
            owner,
            |_| panic!("throttled password cannot be verified"),
            || panic!("throttled client cannot start a terminal"),
            || Ok(AUTHENTICATED_TERMINAL_VT),
        )
        .unwrap();
        let mut response = [0u8; 10];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"THROTTLED\n");
        assert_eq!(tracker.state(), SessionState::Locked);
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
                    || Ok(AUTHENTICATED_TERMINAL_VT),
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
    fn activated_terminal_switching_away_revokes_lease() {
        use std::sync::Arc;
        use std::sync::atomic::{AtomicU16, Ordering};
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let owner = unsafe { native_euid() };
        let active = Arc::new(AtomicU16::new(AUTHENTICATED_TERMINAL_VT));
        let observed = Arc::clone(&active);
        let worker = thread::spawn(move || {
            let mut tracker = SessionTracker::default();
            handle_connection(
                &mut server,
                &mut tracker,
                owner,
                |password| password == b"correct-password",
                || Command::new("sleep").arg("30").spawn(),
                || Ok(observed.load(Ordering::SeqCst)),
            )
            .unwrap();
            tracker.state()
        });
        send_login(&mut client, b"correct-password");
        let mut response = [0u8; 7];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"ACTIVE\n");
        // Wait until the broker has observed tty1 before switching to tty2.
        thread::sleep(Duration::from_millis(250));
        active.store(2, Ordering::SeqCst);
        client
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let mut relock = [0u8; 7];
        client.read_exact(&mut relock).unwrap();
        assert_eq!(&relock, b"LOCKED\n");
        assert_eq!(worker.join().unwrap(), SessionState::Locked);
    }

    #[test]
    fn vt_activation_deadline_and_fail_closed_transition() {
        let mut lease = VtLease::new();
        let start = lease.launched;
        assert!(lease.permits(2, start + Duration::from_secs(1)));
        assert!(!lease.permits(2, start + VT_ACTIVATION_GRACE));
        let mut active = VtLease::new();
        assert!(active.permits(1, active.launched));
        assert!(!active.permits(2, active.launched + Duration::from_millis(10)));
    }

    #[test]
    fn vt_probe_failure_revokes_an_active_session() {
        let (mut client, mut server) = UnixStream::pair().unwrap();
        let owner = unsafe { native_euid() };
        let worker = thread::spawn(move || {
            let mut tracker = SessionTracker::default();
            handle_connection(
                &mut server,
                &mut tracker,
                owner,
                |_| true,
                || Command::new("sleep").arg("30").spawn(),
                || Err(io::Error::other("virtual terminal disappeared")),
            )
            .unwrap();
            tracker.state()
        });
        send_login(&mut client, b"correct-password");
        let mut response = [0u8; 7];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"ACTIVE\n");
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"LOCKED\n");
        assert_eq!(worker.join().unwrap(), SessionState::Locked);
    }

    #[test]
    fn private_stale_socket_can_restart_but_active_socket_is_preserved() {
        let dir = std::env::temp_dir().join(format!("synveil-seat-stale-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, Permissions::from_mode(0o700)).unwrap();
        let owner = fs::metadata(&dir).unwrap().uid();
        let socket = dir.join("seat.sock");
        let first = prepare_seat_socket(&socket, owner).unwrap();
        assert!(prepare_seat_socket(&socket, owner).is_err());
        drop(first);
        assert!(
            fs::symlink_metadata(&socket)
                .unwrap()
                .file_type()
                .is_socket()
        );
        let restarted = prepare_seat_socket(&socket, owner).unwrap();
        assert!(restarted.local_addr().unwrap().as_pathname().is_some());
        assert_eq!(
            fs::metadata(&socket).unwrap().permissions().mode() & 0o777,
            0o600
        );
        drop(restarted);
        fs::remove_dir_all(dir).unwrap();
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
