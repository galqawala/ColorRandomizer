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

Each reroll also randomizes `VSS_ColorChoice`, the *persisted* Catch-A-Ride
color preference (one of the menu's 8 swatches, per vehicle bay) - the same
value that survives a full game restart and determines which swatch the
menu shows as "currently selected" the next time it's freshly opened.

## What this mod does NOT do

Live vehicle paint and driving the Catch-A-Ride color-picker's on-screen
highlight while it's open were both attempted and removed: vehicle
recoloring left vehicles showing a broken-looking flat model rather than a
real paint job, and moving the picker's live highlight never stuck as an
actual selection while the menu was already open. Randomizing the
*persisted* choice (above) sidesteps both - it's read fresh only when the
menu next opens, not while any menu is live.

## Install

Grab `ColorRandomizer.sdkmod` from the latest release and drop it in your
`sdk_mods` folder, same as any other [PythonSDK](https://bl-sdk.github.io/)
mod.

## License

GPL-3.0 — see [LICENSE](LICENSE).
