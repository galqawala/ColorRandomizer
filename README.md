# ColorRandomizer

Randomizes your character's colors and head accessory the moment you spawn -
plus a keybind (default **Insert**) to reroll on demand. Works in
**Borderlands GOTY Enhanced (BL1E)** and vanilla **Borderlands 1 (BL1)**.

## How it works

Hooks the local player's possession event and calls `SetPlayerUIPreferences`,
the same function the character-customization screen itself calls, with a
random primary/secondary/tertiary color (full-range HSL - every color is
possible) and a random head accessory (or none). Retries for a moment if the
pawn's body isn't fully loaded yet, since calling this too early produces no
visible change.

Press **Insert** (rebindable in the mod menu) to reroll immediately, without
waiting to respawn.

## What this mod does NOT do

Vehicle paint and Catch-A-Ride color-picker randomization were both
attempted and removed: vehicle recoloring left vehicles showing a
broken-looking flat model rather than a real paint job, and the color
picker's swatch highlight could be moved but never actually stuck as a
selection. Out of scope for now.

## Install

Grab `ColorRandomizer.sdkmod` from the latest release and drop it in your
`sdk_mods` folder, same as any other [PythonSDK](https://bl-sdk.github.io/)
mod.

## License

GPL-3.0 — see [LICENSE](LICENSE).
