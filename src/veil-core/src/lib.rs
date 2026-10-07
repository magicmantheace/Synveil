// SPDX-License-Identifier: MPL-2.0

use serde_json::json;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use veil_protocol::{
    CORE_SCHEMA_V1, CoreStatus, ErrorCode, MAX_MESSAGE_BYTES, ProtocolError, Request, Response,
    decode_request, encode_line,
};

pub const DEFAULT_SOCKET_PATH: &str = "/run/synveil/veil-core.sock";

pub fn dispatch(request: Request) -> Response {
    match request.method.as_str() {
        "status" => status(request),
        _ => Response::failure(
            request.id,
            ProtocolError::new(
                ErrorCode::UnknownMethod,
                format!("method is not registered: {}", request.method),
            ),
        ),
    }
}

pub fn serve_path(path: &Path, max_connections: Option<usize>) -> io::Result<()> {
    prepare_socket_path(path)?;
    let listener = UnixListener::bind(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;

    log_event(
        "socket_ready",
        json!({
            "socket": path,
            "protocol": CORE_SCHEMA_V1,
        }),
    );

    let mut accepted = 0usize;
    for incoming in listener.incoming() {
        match incoming {
            Ok(stream) => {
                accepted += 1;
                if let Err(error) = serve_connection(stream) {
                    log_event(
                        "connection_error",
                        json!({ "error": error.to_string() }),
                    );
                }
            }
            Err(error) => {
                log_event("accept_error", json!({ "error": error.to_string() }));
            }
        }

        if max_connections.is_some_and(|limit| accepted >= limit) {
            break;
        }
    }

    drop(listener);
    match fs::remove_file(path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    Ok(())
}

fn status(request: Request) -> Response {
    if request
        .params
        .as_object()
        .is_some_and(|parameters| !parameters.is_empty())
    {
        return Response::failure(
            request.id,
            ProtocolError::new(
                ErrorCode::InvalidParameters,
                "status does not accept parameters",
            ),
        );
    }

    let status = CoreStatus {
        service: "veil-core".to_owned(),
        version: env!("CARGO_PKG_VERSION").to_owned(),
        protocol: CORE_SCHEMA_V1.to_owned(),
        state: "ready".to_owned(),
        git_revision: option_env!("SYNVEIL_GIT_SHA").map(str::to_owned),
    };

    match serde_json::to_value(status) {
        Ok(value) => Response::success(request.id, value),
        Err(_) => Response::failure(
            request.id,
            ProtocolError::new(
                ErrorCode::InternalFailure,
                "could not serialize core status",
            ),
        ),
    }
}

fn serve_connection(stream: UnixStream) -> io::Result<()> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut writer = stream;

    loop {
        let Some(line) = read_bounded_line(&mut reader)? else {
            break;
        };

        let response = match decode_request(&line) {
            Ok(request) => {
                log_event(
                    "request",
                    json!({
                        "id": &request.id,
                        "method": &request.method,
                    }),
                );
                dispatch(request)
            }
            Err(error) => {
                log_event(
                    "request_rejected",
                    json!({ "code": error.code.as_str() }),
                );
                Response::failure("", error)
            }
        };

        let encoded = encode_line(&response)
            .map_err(|error| io::Error::other(format!("response encoding failed: {error}")))?;
        writer.write_all(&encoded)?;
        writer.flush()?;
    }

    Ok(())
}

fn read_bounded_line(reader: &mut BufReader<UnixStream>) -> io::Result<Option<Vec<u8>>> {
    let mut line = Vec::new();

    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            if line.is_empty() {
                return Ok(None);
            }
            break;
        }

        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.map_or(available.len(), |index| index + 1);

        if line.len() + take > MAX_MESSAGE_BYTES + 1 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "protocol message exceeds size limit",
            ));
        }

        line.extend_from_slice(&available[..take]);
        reader.consume(take);

        if newline.is_some() {
            break;
        }

        if line.len() > MAX_MESSAGE_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "protocol message exceeds size limit",
            ));
        }
    }

    if line.last() == Some(&b'\n') {
        line.pop();
    }
    if line.last() == Some(&b'\r') {
        line.pop();
    }

    if line.len() > MAX_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "protocol message exceeds size limit",
        ));
    }

    Ok(Some(line))
}

fn prepare_socket_path(path: &Path) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "socket path has no parent directory",
        ));
    };
    fs::create_dir_all(parent)?;

    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => fs::remove_file(path)?,
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "refusing to replace non-socket path",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }

    Ok(())
}

fn log_event(event: &str, fields: serde_json::Value) {
    eprintln!(
        "{}",
        json!({
            "component": "veil-core",
            "event": event,
            "fields": fields,
        })
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::{BufRead, BufReader, Write};
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::thread;
    use std::time::Duration;
    use veil_protocol::{ErrorCode, Request, decode_response, encode_line};

    static NEXT_TEST_ID: AtomicU64 = AtomicU64::new(1);

    #[test]
    fn status_is_read_only_and_structured() {
        let response = dispatch(Request::new("status-1", "status", json!({})));
        assert!(response.ok);
        let result = response.result.expect("status result");
        assert_eq!(result["service"], "veil-core");
        assert_eq!(result["protocol"], CORE_SCHEMA_V1);
        assert_eq!(result["state"], "ready");
    }

    #[test]
    fn unknown_methods_are_rejected() {
        let response = dispatch(Request::new("bad-1", "root_shell", json!({})));
        assert!(!response.ok);
        assert_eq!(
            response.error.expect("protocol error").code,
            ErrorCode::UnknownMethod
        );
    }

    #[test]
    fn status_rejects_parameters() {
        let response = dispatch(Request::new("status-2", "status", json!({ "extra": true })));
        assert!(!response.ok);
        assert_eq!(
            response.error.expect("protocol error").code,
            ErrorCode::InvalidParameters
        );
    }

    #[test]
    fn unix_socket_round_trip() {
        let path = test_socket_path();
        let server_path = path.clone();
        let server = thread::spawn(move || serve_path(&server_path, Some(1)));

        let mut stream = connect_with_retry(&path);
        let request = Request::new("roundtrip-1", "status", json!({}));
        stream
            .write_all(&encode_line(&request).expect("encode request"))
            .expect("write request");
        stream.flush().expect("flush request");

        let mut response_line = Vec::new();
        BufReader::new(stream)
            .read_until(b'\n', &mut response_line)
            .expect("read response");
        if response_line.last() == Some(&b'\n') {
            response_line.pop();
        }

        let response = decode_response(&response_line).expect("decode response");
        assert!(response.ok);
        assert_eq!(response.id, "roundtrip-1");

        server.join().expect("server thread").expect("server result");
        let _ = fs::remove_dir(path.parent().expect("test socket parent"));
    }

    fn test_socket_path() -> std::path::PathBuf {
        let id = NEXT_TEST_ID.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir()
            .join(format!("synveil-veil-core-test-{}-{id}", std::process::id()))
            .join("veil-core.sock")
    }

    fn connect_with_retry(path: &Path) -> UnixStream {
        for _ in 0..100 {
            match UnixStream::connect(path) {
                Ok(stream) => return stream,
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::NotFound | io::ErrorKind::ConnectionRefused
                    ) =>
                {
                    thread::sleep(Duration::from_millis(5));
                }
                Err(error) => panic!("connect failed: {error}"),
            }
        }
        panic!("server socket was not created");
    }
}
