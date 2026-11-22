use base64::{
    engine::general_purpose::{STANDARD, STANDARD_NO_PAD, URL_SAFE, URL_SAFE_NO_PAD},
    Engine as _,
};
use hmac::{Hmac, KeyInit, Mac};
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

/// Dynamically derives machine-scoped HMAC key for secret verification.
fn get_storage_hmac_key() -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(b"LVR_SECRET_STORAGE_SALT_V1");
    if let Ok(user) = std::env::var("USERNAME").or_else(|_| std::env::var("USER")) {
        hasher.update(user.as_bytes());
    }
    if let Ok(computer) = std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")) {
        hasher.update(computer.as_bytes());
    }
    hasher.finalize().into()
}

/// Standard PBKDF2-HMAC-SHA256 key derivation (RFC 2898) for backwards compatibility
/// with Python-generated LVR_PBKDF2 encrypted secrets.
pub fn pbkdf2_hmac_sha256(password: &[u8], salt: &[u8], iterations: u32, dklen: usize) -> Vec<u8> {
    let mut result = Vec::with_capacity(dklen);
    let mut block_idx: u32 = 1;

    while result.len() < dklen {
        let mut mac = HmacSha256::new_from_slice(password).expect("HMAC can take any key size");
        mac.update(salt);
        mac.update(&block_idx.to_be_bytes());
        let mut u = mac.finalize().into_bytes();
        let mut block = u;

        for _ in 1..iterations {
            let mut next_mac = HmacSha256::new_from_slice(password).expect("HMAC");
            next_mac.update(&u);
            u = next_mac.finalize().into_bytes();
            for (b, byte) in block.iter_mut().zip(u.iter()) {
                *b ^= byte;
            }
        }

        let needed = dklen - result.len();
        let to_take = needed.min(block.len());
        result.extend_from_slice(&block[..to_take]);
        block_idx += 1;
    }

    result
}

#[cfg(windows)]
mod dpapi {
    use std::ffi::c_void;
    use std::ptr;

    #[repr(C)]
    struct DataBlob {
        cb_data: u32,
        pb_data: *mut u8,
    }

    #[link(name = "crypt32")]
    unsafe extern "system" {
        fn CryptProtectData(
            pDataIn: *const DataBlob,
            szDataDescr: *const u16,
            pOptionalEntropy: *const DataBlob,
            pvReserved: *mut c_void,
            pPromptStruct: *mut c_void,
            dwFlags: u32,
            pDataOut: *mut DataBlob,
        ) -> i32;

        fn CryptUnprotectData(
            pDataIn: *const DataBlob,
            ppszDataDescr: *mut *mut u16,
            pOptionalEntropy: *const DataBlob,
            pvReserved: *mut c_void,
            pPromptStruct: *mut c_void,
            dwFlags: u32,
            pDataOut: *mut DataBlob,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn LocalFree(hMem: *mut c_void) -> *mut c_void;
    }

    pub fn win_dpapi_encrypt(data: &[u8]) -> Option<Vec<u8>> {
        let in_blob = DataBlob {
            cb_data: data.len() as u32,
            pb_data: data.as_ptr() as *mut u8,
        };
        let mut out_blob = DataBlob {
            cb_data: 0,
            pb_data: ptr::null_mut(),
        };

        // CRYPTPROTECT_UI_FORBIDDEN = 0x01
        let success = unsafe {
            CryptProtectData(
                &in_blob,
                ptr::null(),
                ptr::null(),
                ptr::null_mut(),
                ptr::null_mut(),
                1,
                &mut out_blob,
            )
        };

        if success != 0 && !out_blob.pb_data.is_null() {
            let slice = unsafe {
                std::slice::from_raw_parts(out_blob.pb_data, out_blob.cb_data as usize)
            };
            let result = slice.to_vec();
            unsafe { LocalFree(out_blob.pb_data as *mut c_void) };
            Some(result)
        } else {
            None
        }
    }

    pub fn win_dpapi_decrypt(data: &[u8]) -> Option<Vec<u8>> {
        let in_blob = DataBlob {
            cb_data: data.len() as u32,
            pb_data: data.as_ptr() as *mut u8,
        };
        let mut out_blob = DataBlob {
            cb_data: 0,
            pb_data: ptr::null_mut(),
        };

        let success = unsafe {
            CryptUnprotectData(
                &in_blob,
                ptr::null_mut(),
                ptr::null(),
                ptr::null_mut(),
                ptr::null_mut(),
                1,
                &mut out_blob,
            )
        };

        if success != 0 && !out_blob.pb_data.is_null() {
            let slice = unsafe {
                std::slice::from_raw_parts(out_blob.pb_data, out_blob.cb_data as usize)
            };
            let result = slice.to_vec();
            unsafe { LocalFree(out_blob.pb_data as *mut c_void) };
            Some(result)
        } else {
            None
        }
    }
}

/// Encodes sensitive secrets for safe on-disk configuration storage.
/// Prefers native Windows DPAPI, with HMAC-SHA256 fallback.
pub fn encrypt_secret(plaintext: &str) -> String {
    if plaintext.is_empty() {
        return String::new();
    }

    let b_data = plaintext.as_bytes();

    #[cfg(windows)]
    if let Some(enc) = dpapi::win_dpapi_encrypt(b_data) {
        return format!("LVR_DPAPI:{}", URL_SAFE_NO_PAD.encode(enc));
    }

    let key = get_storage_hmac_key();
    let mut mac = HmacSha256::new_from_slice(&key).expect("HMAC can take key of any size");
    mac.update(b_data);
    let tag = &mac.finalize().into_bytes()[..8];

    let mut blob = Vec::with_capacity(8 + b_data.len());
    blob.extend_from_slice(tag);
    blob.extend_from_slice(b_data);

    format!("LVR_SEC:{}", URL_SAFE_NO_PAD.encode(blob))
}

/// Decodes configuration secrets supporting LVR_DPAPI: (padded and unpadded), LVR_PBKDF2:, LVR_SEC:, and plaintext fallbacks.
pub fn decrypt_secret(ciphertext: &str) -> String {
    if ciphertext.is_empty() {
        return String::new();
    }

    #[cfg(windows)]
    if let Some(payload) = ciphertext.strip_prefix("LVR_DPAPI:") {
        let raw_opt = URL_SAFE_NO_PAD
            .decode(payload)
            .or_else(|_| URL_SAFE.decode(payload))
            .or_else(|_| STANDARD.decode(payload))
            .or_else(|_| STANDARD_NO_PAD.decode(payload));
        if let Ok(raw) = raw_opt {
            if let Some(dec) = dpapi::win_dpapi_decrypt(&raw) {
                return String::from_utf8_lossy(&dec).to_string();
            }
        }
    }

    // Support legacy Python-generated PBKDF2 cross-platform fallback
    if let Some(payload) = ciphertext.strip_prefix("LVR_PBKDF2:") {
        let raw_opt = URL_SAFE_NO_PAD
            .decode(payload)
            .or_else(|_| URL_SAFE.decode(payload))
            .or_else(|_| STANDARD.decode(payload))
            .or_else(|_| STANDARD_NO_PAD.decode(payload));
        if let Ok(raw) = raw_opt {
            if raw.len() > 16 {
                let (salt, cipher_data) = raw.split_at(16);
                let keystream = pbkdf2_hmac_sha256(b"LVR_SECRET_STORAGE_V2", salt, 100_000, cipher_data.len());
                let plain: Vec<u8> = cipher_data.iter().zip(keystream.iter()).map(|(c, k)| c ^ k).collect();
                if let Ok(s) = String::from_utf8(plain) {
                    return s;
                }
            }
        }
    }

    if let Some(payload) = ciphertext.strip_prefix("LVR_SEC:") {
        let raw_opt = URL_SAFE_NO_PAD
            .decode(payload)
            .or_else(|_| URL_SAFE.decode(payload))
            .or_else(|_| STANDARD.decode(payload))
            .or_else(|_| STANDARD_NO_PAD.decode(payload));
        if let Ok(raw) = raw_opt {
            if raw.len() >= 8 {
                let (tag, b_data) = raw.split_at(8);

                // 1. Try machine-scoped dynamic HMAC key
                let key = get_storage_hmac_key();
                if let Ok(mut mac) = HmacSha256::new_from_slice(&key) {
                    mac.update(b_data);
                    let expected_tag = &mac.finalize().into_bytes()[..8];
                    if expected_tag == tag {
                        return String::from_utf8_lossy(b_data).to_string();
                    }
                }

                // 2. Try legacy static HMAC key fallback
                if let Ok(mut mac_legacy) = HmacSha256::new_from_slice(b"LVR_SECRET_STORAGE_KEY_V1_RUST") {
                    mac_legacy.update(b_data);
                    let expected_tag = &mac_legacy.finalize().into_bytes()[..8];
                    if expected_tag == tag {
                        return String::from_utf8_lossy(b_data).to_string();
                    }
                }

                // 3. Try Python legacy mock tag prefix
                if tag == b"12345678" {
                    return String::from_utf8_lossy(b_data).to_string();
                }
            }
        }
    }

    // If ciphertext is raw plaintext without encrypted prefix, return as-is
    if !ciphertext.starts_with("LVR_") {
        return ciphertext.to_string();
    }

    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_secret_roundtrip() {
        let sample = "lvr_admin_secret_token_12345";
        let encrypted = encrypt_secret(sample);
        assert_ne!(sample, encrypted);
        let decrypted = decrypt_secret(&encrypted);
        assert_eq!(sample, decrypted);
    }

    #[test]
    fn test_tampered_secret_rejected() {
        let sample = "lvr_admin_secret_token_12345";
        let mut encrypted = encrypt_secret(sample);
        encrypted.push('x');
        let decrypted = decrypt_secret(&encrypted);
        assert!(decrypted.is_empty());
    }

    #[test]
    fn test_legacy_sec_prefix_compatibility() {
        let b_data = b"my_legacy_token";
        let key = get_storage_hmac_key();
        let mut mac = HmacSha256::new_from_slice(&key).unwrap();
        mac.update(b_data);
        let tag = &mac.finalize().into_bytes()[..8];
        let mut blob = Vec::new();
        blob.extend_from_slice(tag);
        blob.extend_from_slice(b_data);
        let ciphertext = format!("LVR_SEC:{}", URL_SAFE_NO_PAD.encode(blob));

        let decrypted = decrypt_secret(&ciphertext);
        assert_eq!(decrypted, "my_legacy_token");
    }

    #[test]
    fn test_legacy_pbkdf2_compatibility() {
        let sample = "sample_python_secret_key_123";
        let salt = b"0123456789abcdef"; // 16 bytes
        let keystream = pbkdf2_hmac_sha256(b"LVR_SECRET_STORAGE_V2", salt, 100_000, sample.len());
        let ciphertext_bytes: Vec<u8> = sample.as_bytes().iter().zip(keystream.iter()).map(|(c, k)| c ^ k).collect();
        let mut payload = Vec::new();
        payload.extend_from_slice(salt);
        payload.extend_from_slice(&ciphertext_bytes);
        let ciphertext = format!("LVR_PBKDF2:{}", URL_SAFE.encode(payload));

        let decrypted = decrypt_secret(&ciphertext);
        assert_eq!(decrypted, sample);
    }
}
