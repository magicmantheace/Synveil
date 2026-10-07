// SPDX-License-Identifier: MPL-2.0

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fmt;

pub const CORE_SCHEMA_V1: &str = "synveil.core/v1";
pub const MAX_MESSAGE_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Request {
    pub schema: String,
    pub id: String,
    pub method: String,
    #[serde(default = "empty_object")]
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

fn empty_object() -> Value {
    Value::Object(Default::default())
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
}
