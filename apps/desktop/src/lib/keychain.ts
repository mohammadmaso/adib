/**
 * The provider API key lives in the OS keychain, set by Rust commands
 * (`src-tauri/src/keychain.rs`) — it is never sent to or stored by the
 * engine except as a per-call argument.
 */
import { invoke } from "@tauri-apps/api/core";

export const getApiKey = () => invoke<string | null>("get_api_key");
export const setApiKey = (key: string) => invoke<void>("set_api_key", { key });
export const clearApiKey = () => invoke<void>("clear_api_key");

/** The image provider's key lives in its own keychain slot — cover translation
 *  is configured against a separate (possibly different-vendor) endpoint. */
export const getImageApiKey = () => invoke<string | null>("get_image_api_key");
export const setImageApiKey = (key: string) => invoke<void>("set_image_api_key", { key });
export const clearImageApiKey = () => invoke<void>("clear_image_api_key");
