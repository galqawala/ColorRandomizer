import colorsys
import random
from pathlib import Path

import unrealsdk
from mods_base import SETTINGS_DIR, build_mod, hook
from unrealsdk import logging
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

# Randomizes the paint color of every WillowVehicle (Runner, Racer, etc.) the
# moment it spawns.
#
# Mechanism: WillowVehicle.ServerSetVehicleMaterial(MaterialInstance) - the
# same call the game's own vehicle-material replication already uses -
# confirmed present under the identical name, signature and PostBeginPlay
# call site in both this game's own decompiled class dump and vanilla BL1's,
# so this mod supports both unmodified.
#
# The two paint parameters, "Vehicle_Color" and "Trim_color", were read
# directly out of each game's own veh_runner.upk FName table (not guessed) -
# both games' Runner material declares exactly these two names. Other
# vehicle types (e.g. the DLC Salt Racer) do not have them at all, confirmed
# the same way against their own package - setting an unknown parameter name
# on a MaterialInstanceConstant is a harmless no-op in UE3, so this mod
# simply has no visible effect on those rather than failing.


def random_vivid_color() -> WrappedStruct:
    """A random, reasonably vivid/bright RGB color as a LinearColor struct.

    Hue fully random; saturation and value kept high (0.6-1.0 each) so the
    result reads as an actual paint color rather than something muddy or
    near-black/near-white.
    """
    hue = random.random()
    saturation = random.uniform(0.6, 1.0)
    value = random.uniform(0.6, 1.0)
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    return unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=1.0)


def is_host(vehicle) -> bool:
    """Whether the LOCAL machine has authority over this vehicle actor.

    ServerSetVehicleMaterial sets VehicleMaterialParent, a repnotify field -
    once the HOST sets it, it replicates to every client automatically, and
    each client's own SetVehicleMaterial fires from the replication callback
    (confirmed in WillowVehicle.uc). If every client independently rolled
    and applied its own random color here too, whichever RPC reached the
    server last would silently overwrite the others - gating to the host
    avoids that race entirely, and clients need do nothing at all since the
    color reaches them through ordinary replication.

    ROLE_Authority=3 (Engine.Actor.ENetRole, confirmed in the class dump) -
    an unreadable Role fails toward "not the host" rather than risking that
    race.
    """
    try:
        return int(vehicle.Role) == 3
    except Exception:  # noqa: BLE001
        return False


@hook("WillowGame.WillowVehicle:PostBeginPlay", Type.POST)
def on_vehicle_spawned(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    if not is_host(obj):
        return

    # By the time this POST hook runs, PostBeginPlay's own body has already
    # wrapped the mesh's base material into VehicleMaterial (confirmed in
    # WillowVehicle.uc: `if VehicleMaterial == none: ... VehicleMaterial =
    # new (Outer) MI.Class; VehicleMaterial.SetParent(MI)` happens earlier in
    # this same function) - so it is always populated here, not still none.
    parent = getattr(obj, "VehicleMaterial", None)
    if parent is None or parent.Class is None:
        logging.warning("[RandomVehicleColor] vehicle spawned with no VehicleMaterial yet, skipping")
        return

    try:
        # outer=obj.Outer, not obj itself - matches the game's own `new
        # (Outer) MI.Class` in WillowVehicle.uc, where the bare `Outer`
        # inside a WillowVehicle method means self.Outer (its own containing
        # level/package), not the vehicle actor.
        new_material = unrealsdk.construct_object(parent.Class, obj.Outer)
        new_material.SetParent(parent)
        new_material.SetVectorParameterValue("Vehicle_Color", random_vivid_color())
        new_material.SetVectorParameterValue("Trim_color", random_vivid_color())
        obj.ServerSetVehicleMaterial(new_material)
        logging.info(f"[RandomVehicleColor] recolored {obj.Class.Name}")
    except Exception as ex:  # noqa: BLE001
        logging.warning(f"[RandomVehicleColor] could not recolor {obj.Class.Name}: {ex!r}")


# Gets populated from `build_mod` below
__version__: str
__version_info__: tuple[int, ...]

build_mod(
    hooks=[on_vehicle_spawned],
    settings_file=Path(f"{SETTINGS_DIR}/RandomVehicleColor.json"),
)

logging.info(f"Random Vehicle Color Loaded: {__version__}, {__version_info__}")
