# Example device states

Real and reconstructed `coordinator.data[serial]` entries — the shape
`EzvizLightBulb.status()` returns for one bulb — used as test fixtures
(`tests/test_state.py`) and as living documentation of the real EZVIZ
protocol. Pure derivation logic (`custom_components/ezviz_light/state.py`)
is tested against these without needing a live device, HA, or `pyezvizapi`
installed.

## Raw HA captures (`*.yaml`)

Real Developer Tools → States exports of the live LB1-Color entity
(`friendly_name: LB1(XXXXXXXXX)`), in HA's own YAML attribute-dump format.
Each one pins down one `light_mode` value and, critically, **was taken
after** the `light_mode`-aware `color_mode` property was deployed, so HA's
own reported attributes can be checked for self-consistency, not just the
raw device state:

| File | `light_mode` | HA `color_mode` | Confirms |
|---|---|---|---|
| `white_50proc_6500.yaml` | `white` | `color_temp` | ✅ consistent — CCT control works end to end |
| `rgb_100proc_red_sector.yaml` | `color` | `rgb`, `rgb_color: [255,72,1]` | ✅ consistent — `#FF4801` parses to exactly that tuple |
| `scene_halloween.yaml` | `scene` | `color_temp` (stale 6500K, not the scene's actual red/warm) | ✅ matches the documented "scene playback is invisible" limitation |
| `scene_halloween_timer_30min.yaml` | `scene` | same as above | duplicate of the scheduler capture, minus a populated `plan` |
| `scene_halloween_scheduler.yaml` | `scene` | same as above | richest capture — includes a populated `plan` schedule entry |

Also visible here but **not yet wired up** by this integration — noted for
future extension, not implemented:
- `countdown_on`/`countdown_off` (+ `_enable`/`_disable` variants) — sleep/
  wake timers.
- `plan` — a weekly on/off schedule. `scene_halloween_scheduler.yaml` shows
  a real populated entry: `{t, weekDays: "2,3,4,5,6", enable: 1, action:
  "on", startTime: "08:00", endTime: "08:00", n: "My scheduler"}`.
- `scene` / `scene_conf` — the device's own color/brightness sequencing
  (steps of `{b, c, t, m}` with transition/speed). `scene_halloween*.yaml`
  shows the actual "halloween" scene: two steps cycling `#FF4D00`↔`#FFFF00`.
  This is also where the original "red" capture's filename came from: an
  earlier "sleep" scene had `c: "#FF0000"` embedded in its `stat`.
- `restart`, `reset` — device actions, not light state.

## JSON fixtures used by `tests/test_state.py`

Each is `coordinator.data[serial]`-shaped, with `featureItems` copied
verbatim (not reconstructed) from the matching `*.yaml` above:

- `real_lb1_color_capture.json` — from the original (pre-`light_mode`-
  aware) capture; kept as the regression fixture proving
  `current_color_mode_name` must resolve `light_mode: "color"` to RGB
  even when an older HA attribute snapshot disagreed.
- `real_white_mode_capture.json` — from `white_50proc_6500.yaml`.
- `real_rgb_mode_capture.json` — from `rgb_100proc_red_sector.yaml`.
- `real_scene_mode_capture.json` — from `scene_halloween_scheduler.yaml`
  (the richest of the three scene captures), including its `scene_conf`,
  `scene`, and `plan` payloads for future reference.

## Adding more captures

In Home Assistant, go to Developer Tools → States, find the bulb's light
entity, and copy its full attributes — specifically `ezviz_feature_items_raw`
plus `ezviz_color_temperature_raw`. A capture while `light_mode` is
`"music"` would round out the set (every other mode now has a real,
self-consistent example).
