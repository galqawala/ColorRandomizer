import colorsys
import random
from pathlib import Path

import unrealsdk
from mods_base import SETTINGS_DIR, build_mod, get_pc, hook, keybind
from unrealsdk import logging
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

# Randomizes the player's own colors and head accessory on spawn, plus a
# keybind to reroll on demand.
#
# Mechanism: WillowPlayerController.SetPlayerUIPreferences(name, PrimaryColor,
# SecondaryColor, TertiaryColor, HeadAccessory) - the same function the
# character-customization menu itself calls. Routes itself correctly whether
# called on the host or a client (checks Role internally and either applies
# directly or fires its own server RPC), so no host-gating is needed.
# HeadAccessory is an index into Pawn.BodyClass.HeadAccessoryMeshes
# (-1 = no accessory); PrimaryColor/SecondaryColor/TertiaryColor are
# byte-channel Color structs - confirmed from each field's own var
# declaration in WillowPlayerController.uc/Core/Object.uc.
#
# Confirmed present under the same names in both BL1 vanilla's own
# decompiled class dump and BL1E's - this mod supports both without any
# per-game branching.
#
# Vehicle paint and Catch-A-Ride color-picker randomization were both
# attempted and removed (2026-08-16): vehicle recoloring left vehicles
# showing a broken-looking flat blue/gray model rather than a real paint
# job, and the picker's swatch highlight could be moved (confirmed via a
# real unrealsdk.calls.tsv trace) but never actually committed as a
# selection - out of scope for now.


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
    near-grey results, per explicit instruction. True HSL
    (colorsys.hls_to_rgb, h/l/s argument order) rather than HSV, as
    specifically requested - they are different models: HSL's L=1.0 is
    white regardless of saturation, HSV's V=1.0 at S=1.0 is a pure vivid
    color.
    """
    hue = random.random()
    lightness = random.random()
    saturation = random.random()
    return colorsys.hls_to_rgb(hue, lightness, saturation)


def random_byte_color() -> WrappedStruct:
    """A random color as a Color struct (byte 0-255 channels) - what
    SetPlayerUIPreferences expects for the player's own colors."""
    r, g, b = random_hsl_rgb()
    return unrealsdk.make_struct("Color", R=int(r * 255), G=int(g * 255), B=int(b * 255), A=255)


# Confirmed directly, twice: a real trace showed extSetupCell firing 8 times
# per vehicle bay, and separately the user's own observation of the actual
# Catch-A-Ride menu ("out of the 8 options"). Not read dynamically here -
# ColorNavigator.Cells (the live count) only ever exists while a
# VehicleSpawnStationGFxMovie is actually open, which is never the case at
# the point recolor_player() runs.
VSS_SWATCH_COUNT = 8


def player_pawn_is_ready(pawn) -> bool:
    """Whether this pawn's own body/materials setup looks complete enough to
    recolor meaningfully.

    Confirmed in play: calling SetPlayerUIPreferences() right as Possess()
    fires does not throw and does set WillowPlayerReplicationInfo's color
    fields, but produces no visible change - logged evidence was
    Pawn.BodyClass.HeadAccessoryMeshes reading as 0-length both times, which
    only happens for a genuinely empty body class OR one that has not
    finished loading yet. Since UpdatePreferredColors() (called internally,
    applies the ACTUAL mesh material parameters) reads through this same
    BodyClass, an unpopulated one is the leading suspect for the silent
    no-op. Treating "no BodyClass yet" as "not ready" and retrying, rather
    than accepting whatever Possess() handed us immediately, costs nothing
    when it turns out BodyClass was already fine.
    """
    return getattr(pawn, "BodyClass", None) is not None


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

        # Also randomize the PERSISTED Catch-A-Ride color choice. Confirmed
        # by the user directly: this value survives a full game restart and
        # is what the menu shows as "currently selected" every time it's
        # freshly opened - WillowPlayerController.uc syncs it both ways with
        # the player's PlayerProfile (Profile.VSS_ColorChoice[I] <->
        # VSS_ColorChoice[I]) on save/load, exactly the kind of save-data
        # storage the user suspected.
        #
        # An earlier attempt at this exact field was abandoned as "no
        # connection to the display" - but that attempt only ever wrote it
        # from inside the VSS menu's own Start()/extSetupCell hooks, i.e.
        # WHILE the menu was already open, after its in-memory display state
        # (VSSVM_Index) had already been initialized from the OLD value for
        # that session. It was never tested against a freshly-(re)opened
        # menu, which - per the user's own restart observation - is the
        # only time this field actually gets read. Setting it here instead,
        # at spawn (well before any menu could exist yet), sidesteps that
        # ordering problem entirely.
        #
        # Fixed-size array (int[2]) - pyunrealsdk exposes it as an immutable
        # tuple, so the whole tuple is reassigned, never an element
        # (confirmed gotcha, see CLAUDE.md).
        vss_choice = (
            random.randint(0, VSS_SWATCH_COUNT - 1),
            random.randint(0, VSS_SWATCH_COUNT - 1),
        )
        controller.VSS_ColorChoice = vss_choice

        logging.info(
            f"[ColorRandomizer] rerolled player colors, head={head_index} (of {head_count},"
            f" body_class={'set' if body_class is not None else 'NONE'}), VSS_ColorChoice={vss_choice}"
        )
        return True
    except Exception as ex:  # noqa: BLE001
        logging.warning(f"[ColorRandomizer] could not reroll player colors: {ex!r}")
        return False


# The pawn most recently possessed but not yet successfully recolored, or
# None once it has been (or there is nothing pending). Read by the throttled
# retry hook below - see its docstring for why a retry exists at all.
pending_pawn = None
ticks_until_retry = 0
RETRY_INTERVAL_TICKS = 30


@hook("WillowGame.WillowPlayerController:Possess", Type.POST)
def on_player_possessed(
    obj: UObject,
    args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Mark the newly-possessed pawn as needing a recolor.

    Fires once per Possess() call (game start, respawn after death, level
    transitions) - not every tick. An earlier version of this hook was
    WillowPawn:PostBeginPlay, filtered to the local player's own pawn via
    `get_pc().Pawn is obj` - confirmed in play NOT to fire at actual game
    start, because `get_pc().Pawn` was not yet linked back to the
    newly-created pawn at the exact instant PostBeginPlay ran on it. Before
    that, WillowPlayerController:PlayerTick worked but hooked every single
    rendered frame forever (see CLAUDE.md's PlayerTick rule).

    Possess(Pawn aPawn, bool bVehicleTransition) sidesteps the pawn/
    controller link race - it fires exactly when the controller takes
    ownership of aPawn, so there is nothing to wait for there. But
    confirmed in play (2026-08-16): calling SetPlayerUIPreferences() this
    early does not throw, yet produces no visible change and reads
    Pawn.BodyClass.HeadAccessoryMeshes as empty - the pawn's own body/
    material setup is apparently not finished yet even though the pawn
    object itself exists and is possessed. This hook only records that a
    recolor is owed; on_recolor_retry below applies it once the pawn
    actually looks ready, retrying at a low rate rather than every tick.

    bVehicleTransition=True is skipped entirely - confirmed in the BL1E
    dump (Engine/Vehicle.uc): entering a vehicle calls
    `Controller.Possess(self, true)` (the VEHICLE becomes Pawn), and
    exiting calls `Controller.Possess(Driver, true)` to hand control back.
    A vehicle has no BodyClass at all (that's a WillowPlayerPawn-only
    field), so player_pawn_is_ready() could never return True for it -
    without this check, entering a vehicle set pending_pawn to the
    vehicle and on_recolor_retry then logged "pawn not ready yet, will
    retry" every RETRY_INTERVAL_TICKS for as long as the player kept
    driving, reported as log spam while in a vehicle. A vehicle transition
    is not a new spawn anyway - the same character, same colors - so there
    is nothing to reroll here regardless of the spam.
    """
    global pending_pawn, ticks_until_retry
    controller = get_pc()
    if controller is not obj:
        return
    if bool(getattr(args, "bVehicleTransition", False)):
        return
    pending_pawn = obj.Pawn
    ticks_until_retry = 0  # try on the very next opportunity, not after a full interval


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_recolor_retry(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Apply the pending recolor once the possessed pawn looks ready.

    This DOES hook PlayerTick, which CLAUDE.md's own rule says to avoid -
    justified here because it is the rule's own explicitly-named escape
    hatch ("where a tick hook is genuinely unavoidable, gate it behind a
    counter/timer so the real work runs far less often than every frame"):
    there is no single dedicated event confirmed to fire only once the
    pawn's body/materials are actually ready (Possess fires too early - see
    on_player_possessed above), so this polls for readiness instead of for
    the spawn itself, at a throttled rate, and does entirely nothing
    (`pending_pawn is None`) once resolved instead of running every frame
    for the rest of the session.
    """
    global pending_pawn, ticks_until_retry
    if pending_pawn is None:
        return
    ticks_until_retry -= 1
    if ticks_until_retry > 0:
        return
    ticks_until_retry = RETRY_INTERVAL_TICKS

    if obj.Pawn is not pending_pawn:
        # The pending pawn was replaced (e.g. died again) before it was ever
        # successfully recolored - drop it and let the newer Possess() call
        # (which already reset ticks_until_retry) take over.
        pending_pawn = None
    elif not player_pawn_is_ready(pending_pawn):
        logging.info("[ColorRandomizer] pawn not ready yet, will retry")
    else:
        recolor_player(obj)
        pending_pawn = None


@keybind(
    "Reroll Colors",
    "Insert",
    description="Reroll your character's colors and head accessory.",
)
def reroll_colors() -> None:
    controller = get_pc()
    if controller is None:
        return
    recolor_player(controller)


# Gets populated from `build_mod` below
__version__: str
__version_info__: tuple[int, ...]

build_mod(
    hooks=[
        on_player_possessed,
        on_recolor_retry,
    ],
    keybinds=[reroll_colors],
    settings_file=Path(f"{SETTINGS_DIR}/ColorRandomizer.json"),
)

logging.info(f"Color Randomizer Loaded: {__version__}, {__version_info__}")
