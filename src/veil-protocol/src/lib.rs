// SPDX-License-Identifier: MPL-2.0

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fmt;
use std::io::{self, BufRead};

pub const CORE_SCHEMA_V1: &str = "synveil.core/v1";
pub const MAX_MESSAGE_BYTES: usize = 64 * 1024;
pub const BUILD_VERSION: &str = env!("SYNVEIL_BUILD_VERSION");
pub const BUILD_REVISION: Option<&str> = option_env!("SYNVEIL_BUILD_REVISION");

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Request {
    pub schema: String,
    pub id: String,
    pub method: String,
    pub params: Value,
}

impl Request {
    pub fn new(id: impl Into<String>, method: impl Into<String>, params: Value) -> Self {
        Self {
            schema: CORE_SCHEMA_V1.to_owned(),
            id: id.into(),
            method: method.into(),
            params,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Response {
    pub schema: String,
    pub id: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ProtocolError>,
}

impl Response {
    pub fn success(id: impl Into<String>, result: Value) -> Self {
        Self {
            schema: CORE_SCHEMA_V1.to_owned(),
            id: id.into(),
            ok: true,
            result: Some(result),
            error: None,
        }
    }

    pub fn failure(id: impl Into<String>, error: ProtocolError) -> Self {
        Self {
            schema: CORE_SCHEMA_V1.to_owned(),
            id: id.into(),
            ok: false,
            result: None,
            error: Some(error),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    MalformedMessage,
    MessageTooLarge,
    UnsupportedSchema,
    UnknownMethod,
    InvalidParameters,
    InternalFailure,
}

impl ErrorCode {
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::MalformedMessage => "malformed_message",
            Self::MessageTooLarge => "message_too_large",
            Self::UnsupportedSchema => "unsupported_schema",
            Self::UnknownMethod => "unknown_method",
            Self::InvalidParameters => "invalid_parameters",
            Self::InternalFailure => "internal_failure",
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProtocolError {
    pub code: ErrorCode,
    pub message: String,
}

impl ProtocolError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for ProtocolError {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoreStatus {
    pub service: String,
    pub version: String,
    pub protocol: String,
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub git_revision: Option<String>,
}

pub fn decode_request(bytes: &[u8]) -> Result<Request, ProtocolError> {
    enforce_size(bytes)?;

    let request: Request = serde_json::from_slice(bytes).map_err(|_| {
        ProtocolError::new(
            ErrorCode::MalformedMessage,
            "request is not a valid protocol JSON object",
        )
    })?;

    if request.schema != CORE_SCHEMA_V1 {
        return Err(ProtocolError::new(
            ErrorCode::UnsupportedSchema,
            format!("unsupported protocol schema: {}", request.schema),
        ));
    }

    if request.id.trim().is_empty() {
        return Err(ProtocolError::new(
            ErrorCode::MalformedMessage,
            "request id must not be empty",
        ));
    }

    if request.method.trim().is_empty() {
        return Err(ProtocolError::new(
            ErrorCode::MalformedMessage,
            "request method must not be empty",
        ));
    }

    if !request.params.is_object() {
        return Err(ProtocolError::new(
            ErrorCode::InvalidParameters,
            "request params must be a JSON object",
        ));
    }

    Ok(request)
}

pub fn decode_response(bytes: &[u8]) -> Result<Response, ProtocolError> {
    enforce_size(bytes)?;

    let response: Response = serde_json::from_slice(bytes).map_err(|_| {
        ProtocolError::new(
            ErrorCode::MalformedMessage,
            "response is not a valid protocol JSON object",
        )
    })?;

    if response.schema != CORE_SCHEMA_V1 {
        return Err(ProtocolError::new(
            ErrorCode::UnsupportedSchema,
            format!("unsupported protocol schema: {}", response.schema),
        ));
    }

    let shape_is_valid = if response.ok {
        response.result.is_some() && response.error.is_none()
    } else {
        response.result.is_none() && response.error.is_some()
    };

    if !shape_is_valid {
        return Err(ProtocolError::new(
            ErrorCode::MalformedMessage,
            "response success/error fields are inconsistent",
        ));
    }

    Ok(response)
}

pub fn encode_line<T: Serialize>(message: &T) -> Result<Vec<u8>, serde_json::Error> {
    let mut bytes = serde_json::to_vec(message)?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn enforce_size(bytes: &[u8]) -> Result<(), ProtocolError> {
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolError::new(
            ErrorCode::MessageTooLarge,
            format!("message exceeds {MAX_MESSAGE_BYTES} byte limit"),
        ));
    }
    Ok(())
}

/// Read one newline-terminated frame without buffering beyond the wire limit.
/// EOF is clean only between frames, never in the middle of a message.
pub fn read_frame(reader: &mut impl BufRead) -> io::Result<Option<Vec<u8>>> {
    let mut frame = Vec::new();
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return if frame.is_empty() {
                Ok(None)
            } else {
                Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "unterminated protocol frame",
                ))
            };
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let count = newline.unwrap_or(available.len());
        if frame.len() + count > MAX_MESSAGE_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "protocol frame exceeds size limit",
            ));
        }
        frame.extend_from_slice(&available[..count]);
        reader.consume(count + usize::from(newline.is_some()));
        if newline.is_some() {
            return Ok(Some(frame));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn request_round_trip() {
        let request = Request::new("test-1", "status", json!({}));
        let encoded = encode_line(&request).expect("encode request");
        let decoded = decode_request(&encoded[..encoded.len() - 1]).expect("decode request");
        assert_eq!(decoded, request);
    }

    #[test]
    fn rejects_unknown_schema() {
        let request = json!({
            "schema": "synveil.core/v999",
            "id": "test-2",
            "method": "status",
            "params": {}
        });
        let error = decode_request(request.to_string().as_bytes()).expect_err("schema rejected");
        assert_eq!(error.code, ErrorCode::UnsupportedSchema);
    }

    #[test]
    fn rejects_non_object_params() {
        let request = json!({
            "schema": CORE_SCHEMA_V1,
            "id": "test-3",
            "method": "status",
            "params": []
        });
        let error = decode_request(request.to_string().as_bytes()).expect_err("params rejected");
        assert_eq!(error.code, ErrorCode::InvalidParameters);
    }

    #[test]
    fn rejects_oversized_message_before_json_parse() {
        let bytes = vec![b'x'; MAX_MESSAGE_BYTES + 1];
        let error = decode_request(&bytes).expect_err("oversized request rejected");
        assert_eq!(error.code, ErrorCode::MessageTooLarge);
    }

    #[test]
    fn response_shape_is_validated() {
        let invalid = json!({
            "schema": CORE_SCHEMA_V1,
            "id": "test-4",
            "ok": true,
            "error": {
                "code": "internal_failure",
                "message": "impossible"
            }
        });
        let error = decode_response(invalid.to_string().as_bytes()).expect_err("shape rejected");
        assert_eq!(error.code, ErrorCode::MalformedMessage);
    }

    #[test]
    fn framing_preserves_sequential_messages_across_small_buffers() {
        let mut reader = io::BufReader::with_capacity(2, io::Cursor::new(b"one\ntwo\n"));
        assert_eq!(read_frame(&mut reader).unwrap(), Some(b"one".to_vec()));
        assert_eq!(read_frame(&mut reader).unwrap(), Some(b"two".to_vec()));
        assert_eq!(read_frame(&mut reader).unwrap(), None);
    }

    #[test]
    fn framing_requires_a_terminating_newline() {
        let mut reader = io::Cursor::new(b"{\"ok\":true}");
        assert_eq!(
            read_frame(&mut reader).unwrap_err().kind(),
            io::ErrorKind::UnexpectedEof
        );
    }

    #[test]
    fn framing_accepts_exact_limit_and_rejects_one_byte_more() {
        let mut bytes = vec![b'x'; MAX_MESSAGE_BYTES];
        bytes.push(b'\n');
        assert_eq!(
            read_frame(&mut io::Cursor::new(&bytes))
                .unwrap()
                .unwrap()
                .len(),
            MAX_MESSAGE_BYTES
        );
        bytes.insert(0, b'x');
        assert_eq!(
            read_frame(&mut io::Cursor::new(&bytes)).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
        bytes.pop();
        assert_eq!(
            read_frame(&mut io::Cursor::new(&bytes)).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn rejects_invalid_utf8_and_missing_required_params() {
        for message in [
            b"\xff\n".as_slice(),
            b"{\"schema\":\"synveil.core/v1\",\"id\":\"one\",\"method\":\"status\"}",
        ] {
            assert_eq!(
                decode_request(message).unwrap_err().code,
                ErrorCode::MalformedMessage
            );
        }
    }
}
