# EZVIZ Light (LB1) — Home Assistant custom integration

Adds light-entity support for EZVIZ smart bulbs (LB1 White/Color) to Home
Assistant. The official core `ezviz` integration only exposes camera
floodlights as lights — it never touches standalone bulbs — so this is a
separate, standalone integration with its own EZVIZ cloud login.

It's built on `pyezvizapi` (the same library core's `ezviz` integration
depends on), which already has an `EzvizLightBulb` class for bulb control,
just not wired into HA core yet.

## What works (v1)

- On/off and toggle, both roughly one cloud round trip (see "Performance" below)
- Brightness (scaled between EZVIZ's 1-100 and HA's 0-255)
- Color temperature and RGB color, **LB1-Color only** — see caveats below.
  LB1-White has neither feature at all, so it stays brightness-only
  automatically (detected per-bulb from its reported features, not
  hardcoded by model name).
- Mode switching between color temperature and RGB mirrors the device's own
  `light_mode` feature ("white"/"color"/"scene"/"music" — confirmed rw):
  setting one writes `light_mode` to match, and the entity's current color
  mode is read back from it, so it should track whatever mode you last set
  from either Home Assistant or the EZVIZ app.
- Color temperature's min/max/step all come from the device's own
  reported range (`color_temperature`'s `dataDesc`) when available, rather
  than hardcoded guesses — confirmed on a live LB1-Color to be
  `{"range_from": 2700, "range_to": 6500, "interval": 10, "unit": "k"}`.
  The `interval` is enforced server-side (confirmed live: a raw value of
  `6334` from HA's slider was rejected outright, `"num not meets interval"`,
  HTTP-level error code 400) — every write is now snapped to that 10K grid
  before being sent, not just clamped to range.
- Automatic discovery of every bulb on the EZVIZ account (`device_category == "lighting"`)
- Reauthentication flow if the stored session expires
- MFA/SMS verification code step during login, if your account requires it
- Every write (on/off, toggle, brightness, color temp, RGB) retries up to
  twice more (1s then 2s backoff, `WRITE_RETRY_DELAYS_SECONDS` in
  `light.py`) if EZVIZ's cloud reports the device didn't ack in time (meta
  code 2009, seen live repeatedly on `color_rgb` writes — a transient
  cloud-to-device push timeout, not a bug in the key/value sent; one retry
  wasn't always enough). If all attempts fail, raises a clean
  `HomeAssistantError` instead of letting the raw `PyEzvizError` reach
  HA's websocket layer as an unhandled traceback.

## Performance

There is no local control and no batch write API (one PUT per feature
key), so the only lever is sending fewer round trips and never making the
UI wait on them. Four things do that:

- **No redundant device fetch.** Constructing `pyezvizapi.EzvizLightBulb`
  internally re-fetches the *entire* device list just to read back a
  `productId` already available from the last poll. Writes go straight to
  `EzvizClient.set_device_feature_by_key` with the cached one instead.
- **No redundant writes.** `state.plan_writes()` drops anything already
  at the requested value and only sends `light_switch` when the bulb
  isn't already on (or when there's nothing else to send). Adjusting an
  already-on bulb used to cost an extra power write every time; changing
  a colour while already in that mode used to re-send `light_mode` too.
- **Coalescing.** Commands merge into a pending map drained by one
  background task, so a slider drag collapses into a single write of its
  final value rather than one round trip per intermediate step. The first
  command in a burst goes out immediately; only follow-ups wait for the
  `WRITE_DEBOUNCE_SECONDS` (0.3s) window. That single drain task also
  serialises writes, so concurrent commands can't land out of order.
- **The UI never waits.** State is applied optimistically and the service
  call returns before the write is sent, rather than after it completes.

That second point went through two iterations. The first attempt still
kicked off a background "confirm" refresh a few seconds after each write
- which turned out to be the cause of a separate bug: EZVIZ's write API
acks once the cloud has *accepted* a command, before the physical device
has necessarily reported its new status back, so that confirming read
would often see stale pre-write data and visibly snap the UI right back
- looking like the slider "never changed" (confirmed live). Investigated
whether `pyezvizapi`'s MQTT push client (`EzvizClient.get_mqtt_client`)
could replace polling with real-time push instead - it turned out to be
EZVIZ's camera alarm/motion-event channel (its payload schema is built
around snapshot images and alert codes), not a general device-state sync
channel, so it doesn't carry bulb state changes and wasn't usable here.
The actual fix was simpler: stop proactively re-verifying writes at all.
Any genuine silent mismatch (API said success, bulb didn't actually apply
it) still self-corrects via the regular 30s coordinator poll, same as the
EZVIZ app presumably trusting its own writes rather than re-verifying
them.

One consequence of writes now happening in the background: a failed write
can no longer be raised as a service-call error, because by then the
service call has already returned. Instead it's logged at error level and
followed by an immediate refresh, so the UI drops the optimistic value
and snaps back to what the bulb actually did - visible feedback, just not
a red toast. Transient failures are still retried first and usually
self-heal without anything being shown at all.

One trade-off from the optimistic-update change: `toggle` used to
re-fetch the bulb's live state first specifically so it would flip
correctly even if the bulb was changed from the EZVIZ app between HA's
30s polls. It now uses the last polled state instead, for speed. If you
toggle right after changing something in the EZVIZ app (within that same
~30s window), it's possible for the toggle to go the "wrong" way once -
the next periodic poll will still catch it up within 30s either way.

## Known limitations

- **"Scene" and "music" modes aren't controllable.** `light_mode` can also
  be "scene" or "music" (set from the EZVIZ app), which this integration
  doesn't drive — HA has no matching color mode for a dynamic effect. While
  one of those is active, the entity falls back to reporting whichever of
  color-temp/RGB this bulb supports, which won't match what's actually
  showing; on/off and brightness still work regardless.
- **Static device list.** Bulbs are enumerated once per config entry setup.
  Adding a new bulb to your EZVIZ account later requires reloading this
  integration's config entry (Settings → Devices & Services → EZVIZ Light
  Bulb → ⋮ → Reload) for it to show up.
- A few behaviors couldn't be confirmed without a live EZVIZ account/device
  and should be watched for after first setup:
  - Whether your account's MFA step actually fires and matches the flow
    implemented here.
  - Whether `device_sub_category` really reports literally `"LB1"` (used
    only for the entity's `model` field — purely cosmetic if it differs).
  - Behavior if the account has zero lighting-category devices (the
    integration will just create zero entities and log nothing special).

## Installation

### Via HACS (recommended)

1. HACS → ⋮ (top right) → **Custom repositories**.
2. Add `https://github.com/Dewsmen/ha-ezviz-light` with category
   **Integration**.
3. Find "EZVIZ Light Bulb" in HACS and install it.
4. Restart Home Assistant.

### Manually

Copy the `custom_components/ezviz_light/` folder into your Home Assistant
config directory so you end up with `/config/custom_components/ezviz_light/`,
then restart Home Assistant. On Home Assistant OS, the Samba share, SSH &
Terminal, or Studio Code Server add-ons all work for getting the files
across.

### Setup

1. Settings → Devices & Services → **Add Integration** → search
   "EZVIZ Light Bulb".
2. Enter the username, password and region (EU / Russia / Custom) of the
   EZVIZ account the bulb is paired to.
3. If prompted, enter the MFA/SMS verification code.
4. The bulb appears as a light entity. Check on/off, toggle, the brightness
   slider, and — on LB1-Color — the colour temperature slider and RGB
   picker against the physical bulb.

## What's been verified

Everything below is checked without needing a live EZVIZ account or
device, so it runs in CI or on a clone:

- All Python files parse cleanly (`python3 -m py_compile`).
- All JSON config files (`manifest.json`, `strings.json`,
  `translations/en.json`) parse as valid JSON.
- The brightness scaling math (`custom_components/ezviz_light/brightness.py`)
  and the color helpers (`custom_components/ezviz_light/color.py` — hex↔RGB
  conversion, raw feature-item lookup) are covered by
  `tests/test_brightness.py` / `tests/test_color.py` and were manually
  verified to pass; both modules have zero imports so they need neither
  `homeassistant` nor `pyezvizapi` installed to test.
- Every `pyezvizapi` class/method/constant this integration calls
  (`EzvizClient.__init__`, `EzvizClient.login`, `EzvizClient.load_light_bulbs`,
  `EzvizLightBulb.power_on/power_off/set_brightness`,
  `DeviceCatagories.LIGHTING`, and the exception types) was cross-checked
  against the library's real source on GitHub
  (`RenierM26/pyEzvizApi`), not guessed.
- The `color_rgb` (hex string, e.g. `#43FF49`) and `light_mode` (enum
  `white`/`color`/`scene`/`music`, both `transportType: rw`) feature keys
  came directly off a live LB1-Color's raw feature list, not a guess.
- All of `light.py`'s color-mode detection/selection/parsing logic lives in
  `custom_components/ezviz_light/state.py` (also dependency-free) and is
  exercised by `tests/test_state.py` against four real Developer Tools →
  States captures in `examples/` (see `examples/README.md`), not
  reconstructions — one per `light_mode` value (`white`/`color`/`scene`,
  `music` tested via a minimal mutation of the `scene` one). Three of
  those captures were taken **after** this logic was deployed, so HA's own
  reported `color_mode`/`rgb_color`/`color_temp_kelvin` attributes could be
  checked for self-consistency, not just the raw device state — all three
  (white→color_temp, color→rgb with `#FF4801`→`(255,72,1)` matching
  exactly, scene→documented fallback) came back consistent, with no bugs
  found.
Run the suite with `pytest tests/` — the pure modules (`brightness.py`,
`color.py`, `state.py`) deliberately have no `homeassistant` or
`pyezvizapi` imports, so the tests need neither installed.

**Not covered by automated tests**, and only verifiable against a real
account and bulb: the login/MFA flow, device discovery, and whether a
given command actually reaches the hardware. A
`pytest-homeassistant-custom-component` tier covering the entity/config
flow layer would be a welcome contribution.
