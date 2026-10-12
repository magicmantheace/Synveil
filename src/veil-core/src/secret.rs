// SPDX-License-Identifier: MPL-2.0
//! Explicitly erase transient authentication material, including on denial.
//!
//! Regular writes to an unused password buffer can be elided by release
//! optimizers. Volatile stores plus a compiler fence ensure that the physical
//! writes survive optimization. This does not lock memory against swapping
//! and is not a substitute for isolating the authentication process.

use std::sync::atomic::{Ordering, compiler_fence};

pub fn clear(bytes: &mut [u8]) {
    for byte in bytes {
        unsafe {
            std::ptr::write_volatile(byte, 0);
        }
    }
    compiler_fence(Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::clear;

    #[test]
    fn clear_overwrites_empty_and_nonempty_secrets() {
        let mut empty = [];
        clear(&mut empty);
        let mut secret = [1u8, 2, 3, 0, 255, 17];
        clear(&mut secret);
        assert_eq!(secret, [0u8; 6]);
    }
}
