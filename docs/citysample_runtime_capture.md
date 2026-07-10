# CitySample Runtime Capture

CitySample applies FastGeo transformations while PIE is being created. Disabling
FastGeo after PIE has started is too late: source actors such as BigCity's
`water_plane` have already been replaced by surrogate components and cannot be
classified by the runtime semantic scanner.

Argus therefore separates runtime capture into three phases.

## 1. Prepare Before PIE

Run this while no PIE session exists:

```text
py "<Argus>/scripts/prepare_runtime_play_session.py"
```

For the CitySample profile this executes:

```text
FastGeo.EnableTransformer 0
```

The legacy `disable_fastgeo_transformer_for_semantic_capture` option is
automatically migrated to this pre-PIE phase. Built-in CitySample lifecycle
commands are trusted engine integration commands and are not removed by the
allowlist used for user-supplied console commands.

The command must run before the editor creates the PIE world.
On success Argus writes a process-bound state file under `output/`. If command
execution or CVar verification fails, preparation raises an error and rolls
FastGeo back to its original value.

## 2. Capture Inside PIE

Start PIE, then run the normal single-frame or batch capture entry point.
Capture-time preparation still controls World Partition loading ranges, player
streaming-source placement, warmup, pause, and semantic stencil inference.
Capture fails closed unless the state file belongs to the current editor
process and the required FastGeo value is still active.

The default CitySample configuration keeps
`restore_player_after_capture=false`. Moving the player streaming source back
immediately after a capture can unload CitySample Mass traffic/crowd chunks
while Mass observers are locked. A captured session should either move forward
to the next pose or stop PIE instead of restoring the old player position.

## 3. Restore After PIE

Stop PIE first, then run:

```text
py "<Argus>/scripts/restore_runtime_play_session.py"
```

For the default CitySample profile, when FastGeo was originally enabled, this
executes:

```text
FastGeo.EnableTransformer 1
```

If FastGeo was already disabled before preparation, Argus restores it to `0`
instead. The state file is removed only after the restored value is verified.

This boundary is intended to map directly to a future UE plugin's pre-PIE and
post-PIE hooks. The pure planning API lives in `argus_core.capture`, while UE
console execution lives in `scripts/argus_components/runtime_session.py`.

## Harbor Verification

The BigCity harbor water source is:

```text
Actor: water_plane
Mesh: /Engine/BasicShapes/Plane
Material: /Game/Prop/kit_ocean/Material/MI_ocean
Data Layer: CITY_VISTA
Semantic class: water
Stencil: 1
Color: (0, 128, 255)
```

With pre-PIE FastGeo preparation and per-capture post-process rebinding, capture
`harbor_water_preplay_fastgeo_off_20260710_05` at `x=-159954.5625`,
`y=-23040.853516` produced 950,368 water pixels, or 45.832 percent of the
1920x1080 semantic mask. Unknown pixels were 0.738 percent.
