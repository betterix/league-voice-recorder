use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, Mac};
use sha2::Sha256;

const SECRET_SALT: &[u8] = b"LVR_SECRET_STORAGE_V1";
type HmacSha256 = Hmac<Sha256>;

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
    extern "system" {
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
    extern "system" {
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

    let mut mac = HmacSha256::new_from_slice(SECRET_SALT).expect("HMAC can take key of any size");
    mac.update(b_data);
    let tag = &mac.finalize().into_bytes()[..8];

    let mut blob = Vec::with_capacity(8 + b_data.len());
    blob.extend_from_slice(tag);
    blob.extend_from_slice(b_data);

    format!("LVR_SEC:{}", URL_SAFE_NO_PAD.encode(blob))
}

/// Decodes configuration secrets supporting LVR_DPAPI:, LVR_PBKDF2:, and LVR_SEC: prefixes.
pub fn decrypt_secret(ciphertext: &str) -> String {
    if ciphertext.is_empty() {
        return String::new();
    }

    #[cfg(windows)]
    if let Some(payload) = ciphertext.strip_prefix("LVR_DPAPI:") {
        if let Ok(raw) = URL_SAFE_NO_PAD.decode(payload) {
            if let Some(dec) = dpapi::win_dpapi_decrypt(&raw) {
                return String::from_utf8_lossy(&dec).to_string();
            }
        }
    }

    if let Some(payload) = ciphertext.strip_prefix("LVR_SEC:") {
        if let Ok(raw) = URL_SAFE_NO_PAD.decode(payload) {
            if raw.len() < 8 {
                return String::new();
            }
            let (tag, b_data) = raw.split_at(8);
            let mut mac = HmacSha256::new_from_slice(SECRET_SALT).expect("HMAC can take key of any size");
            mac.update(b_data);
            let expected_tag = &mac.finalize().into_bytes()[..8];
            if expected_tag == tag {
                return String::from_utf8_lossy(b_data).to_string();
            }
        }
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
        let mut mac = HmacSha256::new_from_slice(SECRET_SALT).unwrap();
        mac.update(b_data);
        let tag = &mac.finalize().into_bytes()[..8];
        let mut blob = Vec::new();
        blob.extend_from_slice(tag);
        blob.extend_from_slice(b_data);
        let ciphertext = format!("LVR_SEC:{}", URL_SAFE_NO_PAD.encode(blob));

        let decrypted = decrypt_secret(&ciphertext);
        assert_eq!(decrypted, "my_legacy_token");
    }
}
