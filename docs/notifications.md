# Notifications

Pi Beacon can emit Freedesktop notifications for Pi lifecycle events. Dunst, Mako, SwayNC and compatible notification daemons work through `notify-send`.

## Defaults

```toml
[notifications]
enabled = true
settled = true
waiting = true
errors = true
minimum_duration_seconds = 10
sound = false
# sound_file = "~/.config/pi-beacon/complete.wav"
# quiet_hours_start = "22:00"
# quiet_hours_end = "08:00"
```

Visual notifications are enabled by default. Sound is opt in so a package install never creates unexpected audio.

## Events

- `settled`: Pi finished a run and is waiting for the next instruction. Runs shorter than `minimum_duration_seconds` stay quiet.
- `waiting`: Pi opened a blocking question or confirmation prompt.
- `error`: Pi ended with an error.

## Sound

Pi Beacon ships a short completion sound generated for the project. It tries these local players in order:

1. `pw-play`
2. `paplay`
3. `canberra-gtk-play`

Set `sound_file` to replace the bundled WAV. Error and settled notifications can play sound; waiting prompts remain visual only.

## Quiet hours

Set both quiet hour values to suppress notifications across that interval. Intervals that cross midnight are supported.

## Manual test

```bash
pi-beacon notify settled --project "Pi Beacon test" --duration 12
```
