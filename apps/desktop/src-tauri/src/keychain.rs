//! The provider API key never touches the engine's project files or logs —
//! it lives in the OS keychain (macOS Keychain, Windows Credential Manager,
//! Secret Service on Linux) and is passed to the engine per-call from here.
//!
//! One key for the whole app rather than per-project: switching providers is
//! rare enough that a single slot is simpler, and `ProviderSettings` (the
//! per-project config) deliberately has no field for it.

const SERVICE: &str = "adib";
const ACCOUNT: &str = "provider-api-key";
//: Cover translation talks to a separately configured image provider, which
//: may be a different vendor entirely — its key gets its own slot rather than
//: overloading the text provider's.
const IMAGE_ACCOUNT: &str = "image-provider-api-key";

fn entry_for(account: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, account).map_err(|e| e.to_string())
}

fn get(account: &str) -> Result<Option<String>, String> {
    match entry_for(account)?.get_password() {
        Ok(key) => Ok(Some(key)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

fn set(account: &str, key: &str) -> Result<(), String> {
    entry_for(account)?.set_password(key).map_err(|e| e.to_string())
}

fn clear(account: &str) -> Result<(), String> {
    match entry_for(account)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub fn get_api_key() -> Result<Option<String>, String> {
    get(ACCOUNT)
}

#[tauri::command]
pub fn set_api_key(key: String) -> Result<(), String> {
    set(ACCOUNT, &key)
}

#[tauri::command]
pub fn clear_api_key() -> Result<(), String> {
    clear(ACCOUNT)
}

#[tauri::command]
pub fn get_image_api_key() -> Result<Option<String>, String> {
    get(IMAGE_ACCOUNT)
}

#[tauri::command]
pub fn set_image_api_key(key: String) -> Result<(), String> {
    set(IMAGE_ACCOUNT, &key)
}

#[tauri::command]
pub fn clear_image_api_key() -> Result<(), String> {
    clear(IMAGE_ACCOUNT)
}
