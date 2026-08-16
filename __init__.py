import colorsys
import random
from pathlib import Path

import unrealsdk
from mods_base import SETTINGS_DIR, build_mod, get_pc, hook, keybind
from unrealsdk import logging
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

# Randomizes vehicle paint on spawn, and the player's own colors/head on
# spawn, plus a keybind to reroll either on demand.
#
# Vehicle mechanism: WillowVehicle.ServerSetVehicleMaterial(MaterialInstance)
# - the same call the game's own vehicle-material replication already uses.
# The two paint parameters, "Vehicle_Color" and "Trim_color", were read
# directly out of each game's own veh_runner.upk FName table (not guessed) -
# both BL1 and BL1E's Runner material declares exactly these two names.
# Other vehicle types (e.g. the DLC Salt Racer) do not have them at all,
# confirmed the same way against their own package - setting an unknown
# parameter name on a MaterialInstanceConstant is a harmless no-op in UE3,
# so this mod simply has no visible effect on those rather than failing.
#
# Player mechanism: WillowPlayerController.SetPlayerUIPreferences(name,
# PrimaryColor, SecondaryColor, TertiaryColor, HeadAccessory) - the same
# function the character-customization menu itself calls. Unlike the vehicle
# call, this one already routes itself correctly whether called on the host
# or a client (checks Role internally and either applies directly or fires
# its own server RPC), so no host-gating is needed for it. HeadAccessory is
# an index into Pawn.BodyClass.HeadAccessoryMeshes (-1 = no accessory);
# PrimaryColor/SecondaryColor/TertiaryColor are byte-channel Color structs,
# not the LinearColor vehicles use - confirmed from each field's own var
# declaration in WillowPlayerController.uc/Core/Object.uc, not assumed from
# the vehicle side.
#
# Both mechanisms were independently confirmed present under the same names
# in BL1 vanilla's own decompiled class dump as well as BL1E's - this mod
# supports both without any per-game branching.


def random_hsl_rgb() -> tuple[float, float, float]:
    """A random RGB triple (0-1 each) using the FULL range of true HSL.

    This is NOT sampled from the game's own character-customization color
    grid - that grid's actual swatch values live inside the menu's
    Scaleform/Flash asset, not in anything the UnrealScript/Python side can
    read (the script side only tracks which cell/index was picked, via
    CellsNavigator in PlayerRegistrationGFxHelper.uc, not the color each
    cell represents). Short of parsing the .swf itself, there's no in-game
    palette to draw from here - these are procedurally generated instead.

    Hue, saturation AND lightness are each drawn from the FULL 0-1 range -
    every possible color is possible, including near-black/near-white/
    near-grey results, per explicit instruction rather than the previous
    version's "looks nice" 0.25-1.0/0.35-1.0 subrange (itself a widening of
    an even narrower 0.6-1.0 range that made everything look neon - see
    CLAUDE.md's "randomize a color" rule for why narrowing this by default
    is wrong). True HSL (colorsys.hls_to_rgb, h/l/s argument order) rather
    than HSV, as specifically requested - they are different models: HSL's
    L=1.0 is white regardless of saturation, HSV's V=1.0 at S=1.0 is a pure
    vivid color.
    """
    hue = random.random()
    lightness = random.random()
    saturation = random.random()
    return colorsys.hls_to_rgb(hue, lightness, saturation)


def random_linear_color() -> WrappedStruct:
    """A random color as a LinearColor struct (float 0-1 channels) - what
    MaterialInstanceConstant.SetVectorParameterValue expects."""
    r, g, b = random_hsl_rgb()
    return unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=1.0)


def random_byte_color() -> WrappedStruct:
    """A random color as a Color struct (byte 0-255 channels) - what
    SetPlayerUIPreferences expects for the player's own colors."""
    r, g, b = random_hsl_rgb()
    return unrealsdk.make_struct("Color", R=int(r * 255), G=int(g * 255), B=int(b * 255), A=255)


def is_host(actor) -> bool:
    """Whether the LOCAL machine has authority over this actor.

    ROLE_Authority=3 (Engine.Actor.ENetRole, confirmed in the class dump) -
    an unreadable Role fails toward "not the host" rather than risking a
    multi-client race (see recolor_vehicle's docstring).
    """
    try:
        return int(actor.Role) == 3
    except Exception:  # noqa: BLE001
        return False


def recolor_vehicle(vehicle) -> bool:
    """Apply a fresh random paint job to one vehicle actor. Returns success.

    Callers decide whether host-gating is needed: the automatic spawn hook
    below gates to the host, because PostBeginPlay fires independently on
    every connected machine's own copy of the same newly-spawned vehicle -
    without gating, every client would roll and push a DIFFERENT random
    color for the identical vehicle at once, and whichever reached the
    server last would silently win, briefly flickering between colors. The
    keybind below does NOT gate: a keypress only ever happens on the one
    machine that pressed it, so there is no competing call to race against,
    and ServerSetVehicleMaterial is a real RPC (unlike AutoContainerMod's
    old UsedBy() bug elsewhere in this collection of mods) - it correctly
    reaches the server and replicates back down even when called by a
    remote client driving the vehicle, just with a brief network round trip
    instead of an instant local change.
    """
    parent = getattr(vehicle, "VehicleMaterial", None)
    if parent is None or parent.Class is None:
        logging.warning("[ColorRandomizer] vehicle has no VehicleMaterial yet, skipping")
        return False
    try:
        # outer=vehicle.Outer, not vehicle itself - matches the game's own
        # `new (Outer) MI.Class` in WillowVehicle.uc, where the bare `Outer`
        # inside a WillowVehicle method means self.Outer (its own containing
        # level/package), not the vehicle actor.
        new_material = unrealsdk.construct_object(parent.Class, vehicle.Outer)
        new_material.SetParent(parent)
        new_material.SetVectorParameterValue("Vehicle_Color", random_linear_color())
        new_material.SetVectorParameterValue("Trim_color", random_linear_color())
        vehicle.ServerSetVehicleMaterial(new_material)
        logging.info(f"[ColorRandomizer] recolored {vehicle.Class.Name}")
        return True
    except Exception as ex:  # noqa: BLE001
        logging.warning(f"[ColorRandomizer] could not recolor {vehicle.Class.Name}: {ex!r}")
        return False


def recolor_player(controller) -> bool:
    """Reroll one player's own colors and head accessory. Returns success.

    SetPlayerUIPreferences checks Role itself and routes correctly either
    way, so this is safe to call from the host or a client, automatically
    or from the keybind, with no gating needed here.
    """
    pawn = controller.Pawn
    if pawn is None:
        return False

    body_class = getattr(pawn, "BodyClass", None)
    head_meshes = getattr(body_class, "HeadAccessoryMeshes", None) if body_class is not None else None
    head_count = len(head_meshes) if head_meshes is not None else 0
    # -1 means "no head accessory" (WillowPlayerPawn.AttachHeadAccessoryMesh's
    # own default/off value) - a valid choice alongside every real index.
    head_index = random.randint(-1, head_count - 1) if head_count > 0 else -1

    try:
        # Same name back - a CHANGED name is what triggers SetPlayerUIPreferences'
        # own "invalid name" check and a Spark analytics event; this call is
        # only ever about color/head, never about renaming the character.
        controller.SetPlayerUIPreferences(
            controller.PlayerPreferredCharacterName,
            random_byte_color(),
            random_byte_color(),
            random_byte_color(),
            head_index,
        )
        logging.info(f"[ColorRandomizer] rerolled player colors, head={head_index} (of {head_count})")
        return True
    except Exception as ex:  # noqa: BLE001
        logging.warning(f"[ColorRandomizer] could not reroll player colors: {ex!r}")
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
    recolor_vehicle(obj)


@hook("WillowGame.WillowPlayerController:Possess", Type.POST)
def on_player_possessed(
    obj: UObject,
    args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Recolor the local player exactly once per possession.

    Fires once per Possess() call (game start, respawn after death, level
    transitions) - not every tick. Two earlier versions of this hook were
    tried and rejected, in order: (1) WillowPawn:PostBeginPlay, filtered to
    the local player's own pawn via `get_pc().Pawn is obj` - confirmed in
    play NOT to fire at actual game start, because `get_pc().Pawn` was not
    yet linked back to the newly-created pawn at the exact instant
    PostBeginPlay ran on it; (2) WillowPlayerController:PlayerTick, tracking
    the last-recolored pawn by identity - this worked, but hooks every
    single rendered frame forever for a check that only ever matters once
    per spawn (see CLAUDE.md's PlayerTick rule). Possess(Pawn aPawn, bool
    bVehicleTransition) hands the newly-possessed pawn directly as an
    argument, sidestepping both problems at once: no per-tick cost, and no
    ordering race, because it fires exactly when the controller takes
    ownership of aPawn - there is nothing to wait for.
    """
    controller = get_pc()
    if controller is not obj:
        return
    recolor_player(controller)


@keybind(
    "Reroll Colors",
    "Insert",
    description=(
        "Reroll your character's colors/head, and your vehicle's paint if"
        " you're currently driving one."
    ),
)
def reroll_colors() -> None:
    controller = get_pc()
    if controller is None:
        return
    recolor_player(controller)

    pawn = controller.Pawn
    vehicle = getattr(pawn, "DrivenVehicle", None) if pawn is not None else None
    if vehicle is not None:
        recolor_vehicle(vehicle)


# Gets populated from `build_mod` below
__version__: str
__version_info__: tuple[int, ...]

build_mod(
    hooks=[on_vehicle_spawned, on_player_possessed],
    keybinds=[reroll_colors],
    settings_file=Path(f"{SETTINGS_DIR}/ColorRandomizer.json"),
)

logging.info(f"Color Randomizer Loaded: {__version__}, {__version_info__}")
