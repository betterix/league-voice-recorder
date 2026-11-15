use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, Mac};
use sha2::Sha256;

const SECRET_SALT: &[u8] = b"LVR_SECRET_STORAGE_V1";
type HmacSha256 = Hmac<Sha256>;

/// Encodes sensitive secrets for safe on-disk configuration storage with HMAC integrity tag.
pub fn encrypt_secret(plaintext: &str) -> String {
    if plaintext.is_empty() {
        return String::new();
    }

    let b_data = plaintext.as_bytes();
    let mut mac = HmacSha256::new_from_slice(SECRET_SALT).expect("HMAC can take key of any size");
    mac.update(b_data);
    let tag = &mac.finalize().into_bytes()[..8];

    let mut blob = Vec::with_capacity(8 + b_data.len());
    blob.extend_from_slice(tag);
    blob.extend_from_slice(b_data);

    format!("LVR_SEC:{}", URL_SAFE_NO_PAD.encode(blob))
}

/// Decodes configuration secrets and verifies HMAC-SHA256 signature tag.
pub fn decrypt_secret(ciphertext: &str) -> String {
    if ciphertext.is_empty() {
        return String::new();
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
        assert!(encrypted.starts_with("LVR_SEC:"));
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
}
