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
        logging.info(
            f"[ColorRandomizer] rerolled player colors, head={head_index} (of {head_count},"
            f" body_class={'set' if body_class is not None else 'NONE'})"
        )
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


# The pawn most recently possessed but not yet successfully recolored, and
# the most recently opened VSS color menu not yet successfully randomized -
# each None once resolved (or nothing pending). Both are read by the same
# throttled retry hook below - see its docstring for why a retry exists at
# all, and try_randomize_vss_choice's docstring for the VSS-specific case.
pending_pawn = None
pending_vss_movie = None
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
    """
    global pending_pawn, ticks_until_retry
    controller = get_pc()
    if controller is not obj:
        return
    pending_pawn = obj.Pawn
    ticks_until_retry = 0  # try on the very next opportunity, not after a full interval


def try_randomize_vss_choice(movie) -> bool:
    """Attempt to point the VSS color menu at a random valid swatch.

    Returns True once resolved (success OR a real error not worth retrying),
    False to mean "not ready yet, try again".

    Our vehicles are recolored by writing material parameters directly
    (recolor_vehicle), completely bypassing the menu's own swatch-index
    system - so WillowPlayerController.VSS_ColorChoice[slot] (the per-bay
    index the menu remembers and re-shows next time it opens, confirmed in
    WillowPlayerController.uc/VehicleSpawnStationGFxMovie.uc: written on
    OnClose, read back on open) never changed, and the menu kept showing
    whatever it defaulted to - confirmed in play as always "blue".

    This does not make the highlighted swatch match the vehicle's actual
    (continuous, not palette-based) paint - by design, per explicit
    instruction, since there is no reliable way to read the real swatch RGB
    values (they live in the menu's Flash asset, not anywhere script-
    readable - see random_hsl_rgb's docstring). It only stops the menu
    always defaulting to the same one.

    ColorNavigator.Cells was assumed populated synchronously by InitButtons()
    (called earlier in the menu's own Start()) - confirmed WRONG in play:
    every one of 5 opens logged "no color cells found", meaning the swatch
    list genuinely is not populated yet at that point, in native code this
    mod cannot see into. Retried at the same throttled rate as
    on_player_possessed's pawn-readiness check, for the same reason.
    """
    return _try_randomize_vss_choice(movie, "tick-retry")


def _try_randomize_vss_choice(movie, source: str) -> bool:
    """As try_randomize_vss_choice, but tagged with which candidate hook
    called it - see the multi-candidate block below for why several
    differently-tagged callers exist at once right now."""
    try:
        navigator = getattr(movie, "ColorNavigator", None)
        cells = getattr(navigator, "Cells", None) if navigator is not None else None
        cell_count = len(cells) if cells is not None else 0
        if cell_count <= 0:
            logging.info(f"[ColorRandomizer] VSS via {source}: no cells yet")
            return False

        controller = getattr(movie, "PlayerOwner", None)
        if controller is None:
            return True  # no owning controller is not something retrying fixes

        choices = [random.randint(0, cell_count - 1) for _ in range(2)]
        controller.VSS_ColorChoice[0] = choices[0]
        controller.VSS_ColorChoice[1] = choices[1]
        logging.info(
            f"[ColorRandomizer] VSS via {source}: {cell_count} swatches -"
            f" set VSS_ColorChoice={choices}"
        )
        return True
    except Exception as ex:  # noqa: BLE001
        logging.warning(f"[ColorRandomizer] VSS via {source}: error {ex!r}")
        return True


# One-shot diagnostic: two prior guesses at "when is ColorNavigator.Cells
# actually populated" (Start()'s own POST hook, then a 30-tick throttled
# retry) were both confirmed wrong in play. Rather than guess a third hook
# blind, capture the REAL call sequence around a menu open with
# unrealsdk.hooks.log_all_calls (writes every Unreal function call to
# unrealsdk.calls.tsv next to unrealsdk.log - "best used in short bursts for
# debugging" per its own docstring). Bounded to ~2 seconds so the .tsv stays
# a manageable size; turned off unconditionally by on_recolor_retry below
# regardless of throttling, since the countdown itself needs frame accuracy.
# DELETE this whole mechanism (this block, the countdown in on_recolor_retry,
# and CALL_TRACE_TICKS) once the real trigger is found and used instead -
# it is a debugging aid, not a permanent feature.
call_trace_ticks_remaining = 0
CALL_TRACE_TICKS = 120  # roughly 2 seconds at 60fps


@hook("WillowGame.VehicleSpawnStationGFxMovie:Start", Type.POST)
def on_vss_menu_opened(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Mark the just-opened VSS color menu as needing its choice randomized."""
    global pending_vss_movie, ticks_until_retry, call_trace_ticks_remaining
    pending_vss_movie = obj
    ticks_until_retry = 0  # try on the very next opportunity, not after a full interval

    unrealsdk.hooks.log_all_calls(True)
    call_trace_ticks_remaining = CALL_TRACE_TICKS
    logging.info("[ColorRandomizer] VSS menu opened - call trace started (~2s)")


# --- Multiple candidate triggers, tried at once instead of one-guess-per-round ---
# Two single-candidate guesses (Start() POST, a 30-tick throttled retry - both
# above) were each shipped and confirmed wrong in play, one round trip apiece.
# Rather than guess a third one blind, every remaining plausible candidate for
# "ColorNavigator.Cells is actually populated by now" is wired up at once,
# each tagged with its own name in the log (_try_randomize_vss_choice's
# `source` param) - whichever one's log line shows a real swatch count
# (not "no cells yet") the soonest/most reliably is the winner. Once known,
# DELETE every candidate below except that one, and the tick-retry fallback
# above should also be removed if a real event turns out to cover it.


@hook("WillowGame.VehicleSpawnStationGFxMovie:extSetupCell", Type.POST)
def on_vss_cell_setup(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Candidate: extSetupCell is the function that actually appends to
    ColorNavigator.Cells (confirmed in VehicleSpawnStationGFxMovie.uc) - it
    fires once per swatch, so re-applying every time is self-correcting:
    whichever call turns out to be the last one leaves the final, complete
    swatch count as the one actually used."""
    _try_randomize_vss_choice(obj, "extSetupCell")


@hook("WillowGame.VehicleSpawnStationGFxMovie:extSetUpVSSPage", Type.POST)
def on_vss_page_setup(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Candidate: extSetUpVSSPage is native (no visible script body), called
    around the same time as Start() sets up the page - trying it separately
    in case its own completion is a better-timed signal than Start()'s."""
    _try_randomize_vss_choice(obj, "extSetUpVSSPage")


@hook("WillowGame.VehicleSpawnStationGFxMovie:InitButtons", Type.POST)
def on_vss_buttons_init(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Candidate: InitButtons is the native function Start() calls directly
    to set everything up - if cells are populated synchronously within it
    (just not yet by the time Start() itself returns, for some reason not
    visible from script), this POST hook would catch it."""
    _try_randomize_vss_choice(obj, "InitButtons")


@hook("WillowGame.VehicleSpawnStationGFxMovie:OnClose", Type.POST)
def on_vss_menu_closed(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Drop a still-pending VSS randomization once its menu closes.

    Without this, a menu opened and closed again before its cells ever
    populated would leave pending_vss_movie pointing at a GFxMovie the game
    may destroy - reading a destroyed object's properties is exactly the
    class of risk CLAUDE.md's own "never read from an object after handing
    it to an API that may destroy it" rule warns about, even though this
    mod never explicitly destroys it itself.
    """
    global pending_vss_movie
    if pending_vss_movie is obj:
        pending_vss_movie = None


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_recolor_retry(
    obj: UObject,
    __args: WrappedStruct,
    __ret: any,
    __func: BoundFunction,
) -> None:
    """Apply pending recolors once their target actually looks ready.

    This DOES hook PlayerTick, which CLAUDE.md's own rule says to avoid -
    justified here because it is the rule's own explicitly-named escape
    hatch ("where a tick hook is genuinely unavoidable, gate it behind a
    counter/timer so the real work runs far less often than every frame"):
    there is no single dedicated event confirmed to fire only once the
    pawn's body/materials, or the VSS menu's color cells, are actually
    ready - both were tried as one-shot hooks first and confirmed in play
    to fire too early - so this polls for readiness instead of for the
    triggering event itself, at a throttled rate, and does entirely nothing
    once both are resolved (`pending_pawn is None and pending_vss_movie is
    None`) instead of running every frame for the rest of the session.
    """
    global pending_pawn, pending_vss_movie, ticks_until_retry, call_trace_ticks_remaining

    if call_trace_ticks_remaining > 0:
        # Frame-accurate countdown, deliberately not subject to the
        # throttling below - see the diagnostic block above
        # on_vss_menu_opened for why this exists.
        call_trace_ticks_remaining -= 1
        if call_trace_ticks_remaining == 0:
            unrealsdk.hooks.log_all_calls(False)
            logging.info("[ColorRandomizer] call trace window closed")

    if pending_pawn is None and pending_vss_movie is None:
        return
    ticks_until_retry -= 1
    if ticks_until_retry > 0:
        return
    ticks_until_retry = RETRY_INTERVAL_TICKS

    if pending_pawn is not None:
        if obj.Pawn is not pending_pawn:
            # The pending pawn was replaced (e.g. died again) before it was
            # ever successfully recolored - drop it and let the newer
            # Possess() call (which already reset ticks_until_retry) take
            # over.
            pending_pawn = None
        elif not player_pawn_is_ready(pending_pawn):
            logging.info("[ColorRandomizer] pawn not ready yet, will retry")
        else:
            recolor_player(obj)
            pending_pawn = None

    if pending_vss_movie is not None and try_randomize_vss_choice(pending_vss_movie):
        pending_vss_movie = None


@keybind(
    "Reroll Colors",
    "Insert",
    description=(
        "Reroll your vehicle's paint if you're driving one, otherwise your"
        " character's colors/head."
    ),
)
def reroll_colors() -> None:
    controller = get_pc()
    if controller is None:
        return

    pawn = controller.Pawn
    vehicle = getattr(pawn, "DrivenVehicle", None) if pawn is not None else None
    if vehicle is not None:
        recolor_vehicle(vehicle)
    else:
        recolor_player(controller)


# Gets populated from `build_mod` below
__version__: str
__version_info__: tuple[int, ...]

build_mod(
    hooks=[
        on_vehicle_spawned,
        on_player_possessed,
        on_recolor_retry,
        on_vss_menu_opened,
        on_vss_cell_setup,
        on_vss_page_setup,
        on_vss_buttons_init,
        on_vss_menu_closed,
    ],
    keybinds=[reroll_colors],
    settings_file=Path(f"{SETTINGS_DIR}/ColorRandomizer.json"),
)

logging.info(f"Color Randomizer Loaded: {__version__}, {__version_info__}")
