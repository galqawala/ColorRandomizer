# RandomVehicleColor

Gives every vehicle (Runner, Racer, etc.) a random paint color the moment it
spawns, in **Borderlands GOTY Enhanced (BL1E)** and vanilla **Borderlands 1
(BL1)**.

## How it works

Hooks `WillowVehicle:PostBeginPlay` and applies a random color to the two
paint parameters the game's own Runner material actually uses -
`Vehicle_Color` and `Trim_color` (confirmed directly from each game's own
`veh_runner.upk`) - through `ServerSetVehicleMaterial`, the same call the
game itself uses to replicate a vehicle's paint to every player. Other
vehicle types without these parameters (e.g. the DLC Salt Racer) are
unaffected rather than erroring.

In multiplayer, only the host rolls and applies the color; it then reaches
every client through ordinary replication, the same way it does for the
host's own vanilla vehicle-paint changes.

## Install

Grab `RandomVehicleColor.sdkmod` from the latest release and drop it in your
`sdk_mods` folder, same as any other [PythonSDK](https://bl-sdk.github.io/)
mod.

## License

GPL-3.0 — see [LICENSE](LICENSE).
