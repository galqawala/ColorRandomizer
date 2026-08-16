# ColorRandomizer

Randomizes your character's colors and head accessory, and every vehicle's
paint job, the moment each spawns - plus a keybind (default **Insert**) to
reroll either on demand. Works in **Borderlands GOTY Enhanced (BL1E)** and
vanilla **Borderlands 1 (BL1)**.

## How it works

- **Vehicles**: hooks `WillowVehicle:PostBeginPlay` and applies a random
  color to the two paint parameters the game's own Runner material actually
  uses - `Vehicle_Color` and `Trim_color` (confirmed directly from each
  game's own `veh_runner.upk`) - through `ServerSetVehicleMaterial`, the
  same call the game itself uses to replicate a vehicle's paint. Other
  vehicle types without these parameters (e.g. the DLC Salt Racer) are
  unaffected rather than erroring.
- **Character**: hooks the local player pawn's spawn and calls
  `SetPlayerUIPreferences`, the same function the character-customization
  screen itself calls, with a random primary/secondary/tertiary color and a
  random head accessory (or none).
- **Reroll keybind**: press **Insert** (rebindable in the mod menu) to
  reroll your character's colors/head immediately, and your current
  vehicle's paint too if you're driving one.

In multiplayer, vehicle recoloring on spawn is host-only (to avoid every
connected client rolling a different color for the same car at once) and
then reaches everyone through ordinary replication. Character recoloring and
the reroll keybind work correctly for both the host and clients.

## Install

Grab `ColorRandomizer.sdkmod` from the latest release and drop it in your
`sdk_mods` folder, same as any other [PythonSDK](https://bl-sdk.github.io/)
mod.

## License

GPL-3.0 — see [LICENSE](LICENSE).
