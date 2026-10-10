# CitySample Runtime Capture

This guide applies to `config/pipeline_citysample.json`. The default
`pipeline_config.json` now targets the generic CaptureLab map; running the
prepare/restore files with no arguments does **not** select CitySample.
Initialize the UE Python import paths as shown in the
[operator guide](operator_guide.md), and use the same explicit configuration
throughout one session. For mechanism diagnosis, see the
[agent runbook](agent_runbook.md); measured limits are in the
[validation baseline](validation/2026-10-09-capture-baseline.md).

CitySample applies FastGeo transformations while PIE is being created. Disabling
FastGeo after PIE has started is too late: source actors such as BigCity's
`water_plane` have already been replaced by surrogate components and cannot be
classified by the runtime semantic scanner.

Argus therefore separates runtime capture into three phases.

## 1. Prepare Before PIE

Run this while no PIE session exists:

```python
CFG = "config/pipeline_citysample.json"
import prepare_runtime_play_session
prepare_runtime_play_session.prepare(CFG)
```

For the CitySample profile this executes:

```text
CitySample.UseSandboxIntro 0
FastGeo.EnableTransformer 0
```

The legacy `disable_fastgeo_transformer_for_semantic_capture` option is
automatically migrated to this pre-PIE phase. Built-in CitySample lifecycle
commands are trusted engine integration commands and are not removed by the
allowlist used for user-supplied console commands.

The command must run before the editor creates the PIE world.
On success Argus writes a process-bound state file under `output/`. If command
execution or CVar verification fails, preparation raises an error and rolls
the supported Intro/FastGeo settings back to their original values.

## 2. Capture Inside PIE

Start PIE, then run an entry point with the same configuration:

```python
import capture_player_view
job = capture_player_view.capture_player_view(CFG)
```

Alternatively use `capture_rgb_and_mask.capture_once(CFG, pose=...)` or
`batch_capture.run_batch(CFG)` for configured poses. Allow editor ticks and
inspect the asynchronous result before starting another job.
Capture-time preparation still controls World Partition loading ranges, player
streaming-source placement, warmup, pause, and semantic stencil inference.
Capture fails closed unless the state file belongs to the current editor
process and the required FastGeo value is still active.

The CitySample preset keeps `restore_player_after_capture=false` to avoid an
immediate pawn return that can trigger another streaming/unloading transition.
The temporary capture ViewTarget is still restored. Mass-related ensures were
observed during earlier investigation, but the cause of two Python GC crashes
has not been established; this setting is not proof that those crashes are
fixed. The player-view entry point disables pawn movement for its own capture.

The existing Photo Mode F9 integration calls the no-argument player-view entry
point. To use F9 in CitySample, the default configuration must also be the
CitySample preset. The explicit Python call above avoids changing the generic
default. CaptureLab has no CitySample Photo Mode input binding.

## 3. Restore After PIE

Stop PIE first, then run:

```python
import restore_runtime_play_session
restore_runtime_play_session.restore(CFG)
```

When both settings were originally enabled, restoration executes:

```text
CitySample.UseSandboxIntro 1
FastGeo.EnableTransformer 1
```

If either setting was already disabled before preparation, Argus restores that
setting to `0` instead. The state file is removed only after the restored values
are verified. This does not provide automatic original-value restoration for
all arbitrary CVars or loading-range adjustments.

This boundary is intended to map directly to a future UE plugin's pre-PIE and
post-PIE hooks. The pure planning API lives in `argus_core.capture`, while UE
console execution lives in `scripts/argus_components/runtime_session.py`.

## Harbor Verification

The following is a historical 2026-07-10 observation, not a fresh test performed
when these instructions were updated. The 2026-10-09 results are recorded in
the validation baseline linked above.

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
