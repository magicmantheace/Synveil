// SPDX-License-Identifier: MPL-2.0
//! JSON decoding that refuses ambiguous object keys at every nesting level.
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use std::fmt;

struct StrictValue(Value);
struct StrictVisitor;

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictVisitor).map(Self)
    }
}

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("JSON with unique object keys")
    }

    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Value, E> {
        Ok(Value::Bool(value))
    }

    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Value, E> {
        Ok(Value::Number(value.into()))
    }

    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Value, E> {
        Ok(Value::Number(value.into()))
    }

    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Value, E> {
        Number::from_f64(value)
            .map(Value::Number)
            .ok_or_else(|| E::custom("nonfinite JSON number"))
    }

    fn visit_str<E: de::Error>(self, value: &str) -> Result<Value, E> {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_string<E: de::Error>(self, value: String) -> Result<Value, E> {
        Ok(Value::String(value))
    }

    fn visit_unit<E: de::Error>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
        let mut values = Vec::new();
        while let Some(value) = access.next_element::<StrictValue>()? {
            values.push(value.0);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Value, A::Error> {
        let mut values = Map::new();
        while let Some(key) = access.next_key::<String>()? {
            // Compare decoded keys before reading the second value. Escaped
            // aliases are duplicates too; never include a private key in errors.
            if values.contains_key(&key) {
                return Err(de::Error::custom("duplicate JSON object key"));
            }
            let value = access.next_value::<StrictValue>()?;
            values.insert(key, value.0);
        }
        Ok(Value::Object(values))
    }
}

pub(crate) fn decode(bytes: &[u8]) -> Result<Value, serde_json::Error> {
    // serde_json retains its recursion limit and rejects trailing input.
    serde_json::from_slice::<StrictValue>(bytes).map(|value| value.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn unique_nested_values_preserve_json_semantics() {
        let original = json!({
            "null": null, "bool": true, "integer": -123,
            "maximum": u64::MAX, "float": 1.25,
            "array": [{"text": "one\ntwo"}, false, null],
            "unicode": "snow ☃",
        });
        let bytes = serde_json::to_vec(&original).unwrap();
        assert_eq!(decode(&bytes).unwrap(), original);
    }

    #[test]
    fn duplicate_keys_are_refused_before_information_is_lost() {
        for bytes in [
            br#"{"pid":1,"pid":2}"#.as_slice(),
            br#"{"pid":1,"\u0070id":2}"#,
            br#"{"fields":{"private-key":1,"private-key":2}}"#,
            br#"{"fields":[{"x":null,"x":false}]}"#,
        ] {
            let error = decode(bytes).unwrap_err().to_string();
            assert!(error.contains("duplicate JSON object key"));
            assert!(!error.contains("private-key"));
        }
    }

    #[test]
    fn malformed_trailing_and_excessively_nested_json_is_refused() {
        for bytes in [b"{} {}".as_slice(), b"{", b"\xff", b"NaN", b"1e999"] {
            assert!(decode(bytes).is_err());
        }
        let deep = format!("{}0{}", "[".repeat(256), "]".repeat(256));
        assert!(decode(deep.as_bytes()).is_err());
        assert!(decode(b"{\"x\":1,\"y\":1}").is_ok());
    }
}
