//! The provider API key never touches the engine's project files or logs —
//! it lives in the OS keychain (macOS Keychain, Windows Credential Manager,
//! Secret Service on Linux) and is passed to the engine per-call from here.
//!
//! One key for the whole app rather than per-project: switching providers is
//! rare enough that a single slot is simpler, and `ProviderSettings` (the
//! per-project config) deliberately has no field for it.

const SERVICE: &str = "adib";
const ACCOUNT: &str = "provider-api-key";

fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, ACCOUNT).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_api_key() -> Result<Option<String>, String> {
    match entry()?.get_password() {
        Ok(key) => Ok(Some(key)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub fn set_api_key(key: String) -> Result<(), String> {
    entry()?.set_password(&key).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn clear_api_key() -> Result<(), String> {
    match entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}
