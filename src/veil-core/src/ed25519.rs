// SPDX-License-Identifier: MPL-2.0
//! Ed25519 signature verification (RFC 8032), verify-only,
//! implemented in-tree.
//!
//! Why in-tree: the A/B updater must authenticate release metadata
//! inside the guest against a pinned publisher key
//! (docs/AB_UPDATER.md), and the workspace's offline dependency
//! registry contains no signature crate to pin. This module is
//! deliberately small and self-contained: SHA-512 (crate::sha512)
//! plus radix-2^51 field arithmetic. It verifies; it never signs,
//! so no secret material is ever handled here.
//!
//! Inputs to verification are public (a pinned public key, a
//! signature, a message), so the double-and-add scalar multiplication
//! is not constant-time and does not need to be: there is no secret
//! whose timing could leak. Correctness is pinned by the RFC 8032
//! section 7.1 vectors and by group-order self-checks in the tests;
//! the guest path is additionally cross-checked end to end against
//! OpenSSL-signed release fixtures in the QEMU proof record.
//!
//! Verification follows RFC 8032 section 5.1.7 with canonical
//! encoding checks: both point encodings must be canonical, the
//! scalar S must be below the group order, and an x=0 point with
//! the sign bit set is rejected. The hash scalar is used unreduced;
//! [H]A = [H mod L]A holds for A in the prime-order subgroup, and A
//! here is the operator-pinned publisher key, not attacker input.

use crate::sha512::Sha512;

const MASK: u64 = (1 << 51) - 1;

/// Field element mod 2^255 - 19, radix 2^51.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Fe([u64; 5]);

fn carry(mut h: [u64; 5]) -> [u64; 5] {
    let mut c;
    c = h[0] >> 51;
    h[0] &= MASK;
    h[1] += c;
    c = h[1] >> 51;
    h[1] &= MASK;
    h[2] += c;
    c = h[2] >> 51;
    h[2] &= MASK;
    h[3] += c;
    c = h[3] >> 51;
    h[3] &= MASK;
    h[4] += c;
    c = h[4] >> 51;
    h[4] &= MASK;
    h[0] += c * 19;
    c = h[0] >> 51;
    h[0] &= MASK;
    h[1] += c;
    h
}

impl Fe {
    fn zero() -> Self {
        Fe([0; 5])
    }

    fn one() -> Self {
        Fe([1, 0, 0, 0, 0])
    }

    fn from_u64(value: u64) -> Self {
        Fe([value & MASK, (value >> 51) & MASK, 0, 0, 0])
    }

    fn add(&self, other: &Fe) -> Fe {
        Fe(carry(std::array::from_fn(|i| self.0[i] + other.0[i])))
    }

    /// 2p in radix 2^51 limbs, the bias that keeps subtraction
    /// non-negative limb-wise for weakly reduced inputs.
    fn two_p() -> [u64; 5] {
        [
            (1 << 52) - 38,
            (1 << 52) - 2,
            (1 << 52) - 2,
            (1 << 52) - 2,
            (1 << 52) - 2,
        ]
    }

    fn sub(&self, other: &Fe) -> Fe {
        let bias = Self::two_p();
        Fe(carry(std::array::from_fn(|i| {
            self.0[i] + bias[i] - other.0[i]
        })))
    }

    fn neg(&self) -> Fe {
        let bias = Self::two_p();
        Fe(carry(std::array::from_fn(|i| bias[i] - self.0[i])))
    }

    fn mul(&self, other: &Fe) -> Fe {
        let a = &self.0;
        let b = &other.0;
        let mut c = [0u128; 9];
        for i in 0..5 {
            for j in 0..5 {
                c[i + j] += u128::from(a[i]) * u128::from(b[j]);
            }
        }
        // Fold coefficients >= 2^255 back with 2^255 = 19 (mod p).
        let mut r = [0u128; 5];
        r.copy_from_slice(&c[..5]);
        r[0] += c[5] * 19;
        r[1] += c[6] * 19;
        r[2] += c[7] * 19;
        r[3] += c[8] * 19;
        // Carry-propagate in u128, then narrow.
        for i in 0..4 {
            r[i + 1] += r[i] >> 51;
            r[i] &= u128::from(MASK);
        }
        r[0] += (r[4] >> 51) * 19;
        r[4] &= u128::from(MASK);
        r[1] += r[0] >> 51;
        r[0] &= u128::from(MASK);
        Fe([
            r[0] as u64,
            r[1] as u64,
            r[2] as u64,
            r[3] as u64,
            r[4] as u64,
        ])
    }

    fn sqr(&self) -> Fe {
        self.mul(self)
    }

    /// Square-and-multiply over a 256-bit exponent given as four
    /// little-endian u64 words.
    fn pow(&self, exp: &[u64; 4]) -> Fe {
        let mut result = Fe::one();
        for bit in (0..256).rev() {
            result = result.sqr();
            if (exp[bit / 64] >> (bit % 64)) & 1 == 1 {
                result = result.mul(self);
            }
        }
        result
    }

    fn inv(&self) -> Fe {
        // p - 2, little-endian u64 words.
        self.pow(&[0xffffffffffffffeb, u64::MAX, u64::MAX, 0x7fffffffffffffff])
    }

    fn to_bytes(self) -> [u8; 32] {
        // Fully reduce: conditionally subtract p (twice for safety).
        let mut h = carry(self.0);
        let p: [i128; 5] = [
            (1i128 << 51) - 19,
            (1i128 << 51) - 1,
            (1i128 << 51) - 1,
            (1i128 << 51) - 1,
            (1i128 << 51) - 1,
        ];
        for _ in 0..2 {
            let mut borrow: i128 = 0;
            let mut t = [0i128; 5];
            for i in 0..5 {
                t[i] = i128::from(h[i]) - p[i] - borrow;
                if t[i] < 0 {
                    t[i] += 1i128 << 51;
                    borrow = 1;
                } else {
                    borrow = 0;
                }
            }
            if borrow == 0 {
                for i in 0..5 {
                    h[i] = t[i] as u64;
                }
            }
        }
        let mut out = [0u8; 32];
        let mut acc: u128 = 0;
        let mut acc_bits: u32 = 0;
        let mut pos = 0usize;
        for limb in h {
            acc |= u128::from(limb) << acc_bits;
            acc_bits += 51;
            while acc_bits >= 8 {
                out[pos] = acc as u8;
                acc >>= 8;
                acc_bits -= 8;
                pos += 1;
            }
        }
        if pos < 32 {
            out[pos] = acc as u8;
        }
        out
    }

    /// Parse the low 255 bits of a little-endian encoding. The
    /// caller masks the sign bit out of the top byte first.
    fn from_bytes(bytes: &[u8; 32]) -> Fe {
        let mut limbs = [0u64; 5];
        let mut acc: u128 = 0;
        let mut acc_bits: u32 = 0;
        let mut limb = 0usize;
        for byte in bytes.iter() {
            acc |= u128::from(*byte) << acc_bits;
            acc_bits += 8;
            if acc_bits >= 51 && limb < 5 {
                limbs[limb] = (acc as u64) & MASK;
                acc >>= 51;
                acc_bits -= 51;
                limb += 1;
            }
        }
        if limb < 5 {
            limbs[limb] = acc as u64 & MASK;
        }
        Fe(limbs)
    }

    fn is_zero(&self) -> bool {
        self.to_bytes() == [0u8; 32]
    }

    fn equals(&self, other: &Fe) -> bool {
        self.to_bytes() == other.to_bytes()
    }
}

/// Curve constants derived at runtime from their small-integer
/// definitions, so no hand-transcribed limb tables can rot.
struct Curve {
    d: Fe,
    d2: Fe,
    sqrt_m1: Fe,
}

impl Curve {
    fn new() -> Self {
        // d = -121665 / 121666 (mod p)
        let d = Fe::from_u64(121665).mul(&Fe::from_u64(121666).inv()).neg();
        let d2 = d.add(&d);
        // sqrt(-1) = 2^((p-1)/4); (p-1)/4 = 2^253 - 5.
        let sqrt_m1 =
            Fe::from_u64(2).pow(&[0xfffffffffffffffb, u64::MAX, u64::MAX, 0x1fffffffffffffff]);
        Curve { d, d2, sqrt_m1 }
    }
}

/// Extended twisted-Edwards point (X, Y, T, Z), a = -1.
#[derive(Clone, Copy)]
struct Point {
    x: Fe,
    y: Fe,
    t: Fe,
    z: Fe,
}

impl Point {
    fn identity() -> Self {
        Point {
            x: Fe::zero(),
            y: Fe::one(),
            t: Fe::zero(),
            z: Fe::one(),
        }
    }

    fn add(&self, other: &Point, curve: &Curve) -> Point {
        let a = self.y.sub(&self.x).mul(&other.y.sub(&other.x));
        let b = self.y.add(&self.x).mul(&other.y.add(&other.x));
        let c = self.t.mul(&other.t).mul(&curve.d2);
        let d = self.z.mul(&other.z).add(&self.z.mul(&other.z));
        let e = b.sub(&a);
        let f = d.sub(&c);
        let g = d.add(&c);
        let h = b.add(&a);
        Point {
            x: e.mul(&f),
            y: g.mul(&h),
            t: e.mul(&h),
            z: f.mul(&g),
        }
    }

    fn double(&self) -> Point {
        let a = self.x.sqr();
        let b = self.y.sqr();
        let c = self.z.sqr().add(&self.z.sqr());
        let d = a.neg();
        let e = self.x.add(&self.y).sqr().sub(&a).sub(&b);
        let g = d.add(&b);
        let f = g.sub(&c);
        let h = d.sub(&b);
        Point {
            x: e.mul(&f),
            y: g.mul(&h),
            t: e.mul(&h),
            z: f.mul(&g),
        }
    }

    /// Double-and-add over a little-endian scalar of up to 64 bytes.
    fn scalarmult(&self, scalar: &[u8], curve: &Curve) -> Point {
        let mut result = Point::identity();
        let mut base = *self;
        for i in 0..scalar.len() * 8 {
            if (scalar[i / 8] >> (i % 8)) & 1 == 1 {
                result = result.add(&base, curve);
            }
            base = base.double();
        }
        result
    }

    #[cfg(test)]
    fn encode(&self) -> [u8; 32] {
        let z_inv = self.z.inv();
        let x = self.x.mul(&z_inv);
        let y = self.y.mul(&z_inv);
        let mut out = y.to_bytes();
        if x.to_bytes()[0] & 1 == 1 {
            out[31] |= 0x80;
        }
        out
    }

    fn projective_equals(&self, other: &Point) -> bool {
        let left_x = self.x.mul(&other.z);
        let right_x = other.x.mul(&self.z);
        let left_y = self.y.mul(&other.z);
        let right_y = other.y.mul(&self.z);
        left_x.equals(&right_x) && left_y.equals(&right_y)
    }
}

/// sqrt(u/v) for p = 5 (mod 8), per RFC 8032 section 5.1.3.
fn sqrt_ratio(u: &Fe, v: &Fe, curve: &Curve) -> Option<Fe> {
    if v.is_zero() {
        return None;
    }
    let v3 = v.sqr().mul(v);
    let v7 = v3.sqr().mul(v);
    // (p - 5) / 8 = 2^252 - 3, little-endian u64 words.
    let x = u
        .mul(&v7)
        .pow(&[0xfffffffffffffffd, u64::MAX, u64::MAX, 0x0fffffffffffffff])
        .mul(u)
        .mul(&v3);
    let check = v.mul(&x.sqr());
    if check.equals(u) {
        return Some(x);
    }
    if check.equals(&u.neg()) {
        return Some(x.mul(&curve.sqrt_m1));
    }
    None
}

/// Decode a canonical point encoding; rejects non-canonical y,
/// points not on the curve, and x = 0 with the sign bit set.
fn decode(bytes: &[u8; 32], curve: &Curve) -> Option<Point> {
    let sign = bytes[31] >> 7;
    let mut masked = *bytes;
    masked[31] &= 0x7f;
    let y = Fe::from_bytes(&masked);
    if y.to_bytes() != masked {
        return None; // non-canonical field encoding
    }
    let y2 = y.sqr();
    let u = y2.sub(&Fe::one());
    let v = y2.mul(&curve.d).add(&Fe::one());
    let x = sqrt_ratio(&u, &v, curve)?;
    let x = if (x.to_bytes()[0] & 1) != sign {
        x.neg()
    } else {
        x
    };
    if x.is_zero() && sign == 1 {
        return None;
    }
    Some(Point {
        x,
        y,
        t: x.mul(&y),
        z: Fe::one(),
    })
}

fn base_point(curve: &Curve) -> Point {
    // Canonical encoding of the Ed25519 base point.
    let mut encoding = [0x66u8; 32];
    encoding[0] = 0x58;
    decode(&encoding, curve).expect("base point encoding is valid")
}

/// Group order L = 2^252 + 27742317777372353535851937790883648493,
/// as little-endian u64 words.
const GROUP_ORDER: [u64; 4] = [
    0x5812631a5cf5d3ed,
    0x14def9dea2f79cd6,
    0,
    0x1000000000000000,
];

fn scalar_below_group_order(s: &[u8; 32]) -> bool {
    let mut words = [0u64; 4];
    for i in 0..4 {
        words[i] = u64::from_le_bytes([
            s[i * 8],
            s[i * 8 + 1],
            s[i * 8 + 2],
            s[i * 8 + 3],
            s[i * 8 + 4],
            s[i * 8 + 5],
            s[i * 8 + 6],
            s[i * 8 + 7],
        ]);
    }
    for i in (0..4).rev() {
        if words[i] != GROUP_ORDER[i] {
            return words[i] < GROUP_ORDER[i];
        }
    }
    false // equal to L is not below L
}

/// Verify an Ed25519 signature over `message` against the 32-byte
/// public key. This is the only operation the A/B updater needs.
pub fn verify(public_key: &[u8; 32], signature: &[u8; 64], message: &[u8]) -> bool {
    let curve = Curve::new();
    let Some(a_point) = decode(public_key, &curve) else {
        return false;
    };
    let mut r_encoding = [0u8; 32];
    r_encoding.copy_from_slice(&signature[..32]);
    let Some(r_point) = decode(&r_encoding, &curve) else {
        return false;
    };
    let mut s_bytes = [0u8; 32];
    s_bytes.copy_from_slice(&signature[32..]);
    if !scalar_below_group_order(&s_bytes) {
        return false;
    }
    let mut hasher = Sha512::new();
    hasher.update(&r_encoding);
    hasher.update(public_key);
    hasher.update(message);
    let hash = hasher.finalize();
    let left = base_point(&curve).scalarmult(&s_bytes, &curve);
    let right = r_point.add(&a_point.scalarmult(&hash, &curve), &curve);
    left.projective_equals(&right)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unhex(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
            .collect()
    }

    #[test]
    fn group_order_self_check() {
        // [L]B is the identity and [L+1]B is B: this pins the group
        // order constant and the whole arithmetic stack at once.
        let curve = Curve::new();
        let base = base_point(&curve);
        let mut l_bytes = [0u8; 32];
        for i in 0..4 {
            l_bytes[i * 8..i * 8 + 8].copy_from_slice(&GROUP_ORDER[i].to_le_bytes());
        }
        let identity = base.scalarmult(&l_bytes, &curve);
        assert_eq!(identity.encode()[0], 1);
        assert!(identity.x.is_zero());
        let mut l_plus = l_bytes;
        l_plus[0] += 1;
        let again = base.scalarmult(&l_plus, &curve);
        assert!(again.projective_equals(&base));
    }

    #[test]
    fn base_point_has_canonical_encoding() {
        let curve = Curve::new();
        let base = base_point(&curve);
        let mut encoding = [0x66u8; 32];
        encoding[0] = 0x58;
        assert_eq!(base.encode(), encoding);
    }

    #[test]
    fn rfc8032_test_1_empty_message() {
        let public = unhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a");
        let signature = unhex(concat!(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155",
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ));
        let mut pk = [0u8; 32];
        pk.copy_from_slice(&public);
        let mut sig = [0u8; 64];
        sig.copy_from_slice(&signature);
        assert!(verify(&pk, &sig, b""));
        assert!(!verify(&pk, &sig, b"\x00"));
        let mut tampered = sig;
        tampered[10] ^= 1;
        assert!(!verify(&pk, &tampered, b""));
    }

    #[test]
    fn openssl_generated_vectors() {
        // Known-answer vectors produced with OpenSSL pkeyutl at
        // implementation time (the private key was discarded; only
        // the public key and signatures are pinned here). They
        // cross-check this implementation against the same OpenSSL
        // that signs release fixtures in tests/ab-update.py.
        let public = unhex("3ea6ed1b5e78238089033caa98f3e42eb41716ac358ae20f56ecda07b1bf66bb");
        let mut pk = [0u8; 32];
        pk.copy_from_slice(&public);

        let short_sig = unhex(concat!(
            "82fa75489cc6b9924a30a2a9a6cc59eb430774c629a1a72a169e2ec0db5b1d64",
            "541b0d9171bac3d645fd351c61dbddfdac61b6aad385eb4d7bef04c8ac167a01"
        ));
        let mut sig = [0u8; 64];
        sig.copy_from_slice(&short_sig);
        let short_msg = unhex("af82");
        assert!(verify(&pk, &sig, &short_msg));
        assert!(!verify(&pk, &sig, b""));

        let long_sig = unhex(concat!(
            "bdd03420f406b53e3ca2a20ac7a31cf74a7d4cf9d4f634996cd6ceff82becaf7",
            "7ea6ad4caf67df28e6cff91dc8664e4a6be3a35ec5cf7ff698004081ee1bc705"
        ));
        let mut sig2 = [0u8; 64];
        sig2.copy_from_slice(&long_sig);
        let long_msg: Vec<u8> = (0u8..=255).collect::<Vec<_>>().repeat(3);
        assert!(verify(&pk, &sig2, &long_msg));
        assert!(!verify(&pk, &sig2, &short_msg));
    }

    #[test]
    fn rejects_out_of_range_scalar_and_bad_points() {
        let curve = Curve::new();
        let base = base_point(&curve);
        let pk = base.encode();
        let mut sig = [0u8; 64];
        sig[..32].copy_from_slice(&pk);
        // S = 0xff.. is far above the group order.
        for byte in &mut sig[32..] {
            *byte = 0xff;
        }
        assert!(!verify(&pk, &sig, b"anything"));
        // Non-canonical y (y = p) must not decode.
        let mut bad = [0u8; 32];
        bad[0] = 0xed;
        for byte in &mut bad[1..31] {
            *byte = 0xff;
        }
        bad[31] = 0x7f;
        assert!(decode(&bad, &curve).is_none());
        let _ = curve;
    }
}
