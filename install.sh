#!/bin/sh
set -eu

umask 077

REPOSITORY="andres-ortizl/pi-beacon"
DEFAULT_VERSION="v1.1.0"
ACTION="install"
ACTION_SET=0
INTERACTIVE_MENU=0
VERSION=${PI_BEACON_VERSION:-$DEFAULT_VERSION}
ASSUME_YES=0
PURGE=0
QUICKSHELL_CONFIG=""
UPDATE_CHECK=-1

CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
CACHE_HOME=${XDG_CACHE_HOME:-"$HOME/.cache"}
STATE_HOME=${XDG_STATE_HOME:-"$HOME/.local/state"}
STATE_DIR="$STATE_HOME/pi-beacon"
STATE_FILE="$STATE_DIR/installer-state"
SERVICE_FILE="$CONFIG_HOME/systemd/user/pi-beacon.service"
UPDATE_SERVICE_FILE="$CONFIG_HOME/systemd/user/pi-beacon-update-check.service"
UPDATE_TIMER_FILE="$CONFIG_HOME/systemd/user/pi-beacon-update-check.timer"
PI_SOURCE_ID="git:github.com/$REPOSITORY"
TEMP_FILE=""

say() {
	printf '%s\n' "$*"
}

fail() {
	printf 'pi-beacon: %s\n' "$*" >&2
	exit 1
}

cleanup_temp() {
	if [ -n "$TEMP_FILE" ]; then
		rm -f "$TEMP_FILE"
	fi
}

trap cleanup_temp 0
trap 'exit 1' HUP INT TERM

usage() {
	cat <<'EOF'
Install, update, or remove Pi Beacon.

Usage:
  install.sh
  install.sh [install|update] [--yes] [--version REF] [--quickshell NAME]
  install.sh uninstall [--yes] [--purge]

Options:
  --yes, -y          Accept required non-interactive confirmations.
  --version REF      Install a release tag or Git ref. Default: v1.1.0.
  --quickshell NAME  Install the maintained QML components into this config.
  --update-check     Enable the opt-in daily release check timer.
  --no-update-check  Disable the daily release check timer.
  --purge            With uninstall, also remove config, cache, runtime data,
                     installer state, and installer-managed Quickshell files.
  --help, -h         Show this help.

Examples:
  curl -fsSL https://raw.githubusercontent.com/andres-ortizl/pi-beacon/v1.1.0/install.sh | sh
  sh install.sh install --yes --quickshell shell
  sh install.sh uninstall --yes --purge
EOF
}

confirm() {
	prompt=$1
	if [ "$ASSUME_YES" -eq 1 ]; then
		return 0
	fi
	if [ ! -r /dev/tty ]; then
		fail "$prompt Re-run with --yes."
	fi
	printf '%s [y/N] ' "$prompt" >/dev/tty
	IFS= read -r answer </dev/tty || answer=""
	case "$answer" in
	y | Y | yes | YES | Yes) return 0 ;;
	*) return 1 ;;
	esac
}

choose_action() {
	[ -r /dev/tty ] || fail "Interactive mode requires a terminal. Re-run with install --yes or uninstall --yes."
	cat >/dev/tty <<'EOF'

Pi Beacon

  1) Install or update
  2) Uninstall
  3) Exit

EOF
	printf 'Choose an action [1]: ' >/dev/tty
	IFS= read -r choice </dev/tty || choice=""
	case "$choice" in
	"" | 1 | i | I | install) ACTION="install" ;;
	2 | u | U | uninstall) ACTION="uninstall" ;;
	3 | q | Q | quit | exit) exit 0 ;;
	*) fail "Unknown menu choice: $choice" ;;
	esac
	ACTION_SET=1
	INTERACTIVE_MENU=1
}

choose_quickshell_config() {
	[ -z "$QUICKSHELL_CONFIG" ] || return 0
	quickshell_root="$CONFIG_HOME/quickshell"
	if ! command -v qs >/dev/null 2>&1 && [ ! -d "$quickshell_root" ]; then
		return 0
	fi
	if [ -d "$quickshell_root" ]; then
		say "Detected Quickshell configurations:" >/dev/tty
		for config_dir in "$quickshell_root"/*; do
			[ -d "$config_dir" ] || continue
			printf '  - %s\n' "${config_dir##*/}" >/dev/tty
		done
	fi
	printf 'Quickshell config name, or Enter to skip: ' >/dev/tty
	IFS= read -r QUICKSHELL_CONFIG </dev/tty || QUICKSHELL_CONFIG=""
	validate_quickshell_name
}

prepare_interactive() {
	if [ "$ASSUME_YES" -ne 0 ]; then
		[ "$UPDATE_CHECK" -ge 0 ] || UPDATE_CHECK=0
		return 0
	fi
	if [ "$ACTION" = "install" ] || [ "$ACTION" = "update" ]; then
		if [ "$INTERACTIVE_MENU" -eq 0 ]; then
			confirm "Install Pi Beacon $VERSION?" || exit 0
		fi
		choose_quickshell_config
		if [ "$UPDATE_CHECK" -lt 0 ]; then
			if confirm "Check once per day for new Pi Beacon releases?"; then
				UPDATE_CHECK=1
			else
				UPDATE_CHECK=0
			fi
		fi
		return 0
	fi
	confirm "Uninstall Pi Beacon?" || exit 0
	if [ "$PURGE" -eq 1 ]; then
		confirm "Permanently remove Pi Beacon config and cache?" || exit 0
	elif confirm "Also remove local config and cache?"; then
		PURGE=1
	fi
}

need_command() {
	command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

validate_ref() {
	case "$VERSION" in
	"" | *[!A-Za-z0-9._/-]*) fail "Invalid version or Git ref: $VERSION" ;;
	esac
}

validate_quickshell_name() {
	[ -z "$QUICKSHELL_CONFIG" ] && return 0
	case "$QUICKSHELL_CONFIG" in
	"." | "..") fail "Invalid Quickshell config name: $QUICKSHELL_CONFIG" ;;
	*[!A-Za-z0-9._-]*) fail "Invalid Quickshell config name: $QUICKSHELL_CONFIG" ;;
	esac
}

ensure_uv() {
	if command -v uv >/dev/null 2>&1; then
		return 0
	fi
	need_command curl
	need_command mktemp
	confirm "uv is required. Install uv from astral.sh?" || fail "uv installation declined"
	say "Installing uv..."
	TEMP_FILE=$(mktemp "${TMPDIR:-/tmp}/pi-beacon-uv.XXXXXX") ||
		fail "Could not create a temporary uv installer file"
	if ! curl -LsSf -o "$TEMP_FILE" https://astral.sh/uv/install.sh; then
		fail "Failed to download the complete uv installer"
	fi
	sh "$TEMP_FILE"
	rm -f "$TEMP_FILE"
	TEMP_FILE=""
	PATH="$HOME/.local/bin:$PATH"
	export PATH
	command -v uv >/dev/null 2>&1 || fail "uv installation completed but uv is not on PATH"
}

save_state() {
	state_configs=$(tracked_quickshell_configs)
	seen_current=0
	mkdir -p "$STATE_DIR"
	{
		printf 'version=%s\n' "$VERSION"
		printf 'update_check=%s\n' "$UPDATE_CHECK"
		for config_name in $state_configs; do
			case "$config_name" in
			"." | ".." | *[!A-Za-z0-9._-]*)
				fail "Unsafe Quickshell config in installer state"
				;;
			esac
			printf 'quickshell=%s\n' "$config_name"
			if [ "$config_name" = "$QUICKSHELL_CONFIG" ]; then
				seen_current=1
			fi
		done
		if [ -n "$QUICKSHELL_CONFIG" ] && [ "$seen_current" -eq 0 ]; then
			printf 'quickshell=%s\n' "$QUICKSHELL_CONFIG"
		fi
	} >"$STATE_FILE"
}

install_quickshell_assets() {
	installed_configs=""
	for config_name in $(tracked_quickshell_configs) $QUICKSHELL_CONFIG; do
		[ -n "$config_name" ] || continue
		case "$config_name" in
		"." | ".." | *[!A-Za-z0-9._-]*)
			fail "Unsafe Quickshell config in installer state"
			;;
		esac
		case " $installed_configs " in
		*" $config_name "*) continue ;;
		esac
		pi-beacon install-quickshell "$config_name" --force
		installed_configs="$installed_configs $config_name"
	done
}

configure_update_timer() {
	if [ "$UPDATE_CHECK" -eq 1 ]; then
		pi-beacon install-update-timer --force
		return 0
	fi
	if [ -f "$UPDATE_SERVICE_FILE" ] || [ -f "$UPDATE_TIMER_FILE" ]; then
		systemctl --user disable --now pi-beacon-update-check.timer ||
			fail "Could not disable the Pi Beacon update timer"
		rm -f "$UPDATE_SERVICE_FILE" "$UPDATE_TIMER_FILE"
	fi
}

install_beacon() {
	[ "$(uname -s)" = "Linux" ] || fail "Pi Beacon currently supports Linux only"
	[ "$(id -u)" -ne 0 ] || fail "Run this installer as your desktop user, not root"
	need_command pi
	need_command systemctl
	ensure_uv
	PATH="$HOME/.local/bin:$PATH"
	export PATH

	backend_source="git+https://github.com/$REPOSITORY.git@$VERSION"
	pi_source="$PI_SOURCE_ID@$VERSION"

	say "Installing Pi Beacon backend $VERSION..."
	uv tool install --force "$backend_source"
	command -v pi-beacon >/dev/null 2>&1 || fail "pi-beacon was not installed on PATH"

	if [ ! -f "$CONFIG_HOME/pi-beacon/config.toml" ]; then
		pi-beacon init-config
	else
		say "Keeping existing config: $CONFIG_HOME/pi-beacon/config.toml"
	fi

	pi-beacon install-systemd --force
	configure_update_timer
	systemctl --user daemon-reload
	systemctl --user enable --now pi-beacon.service
	if [ "$UPDATE_CHECK" -eq 1 ]; then
		systemctl --user enable --now pi-beacon-update-check.timer
	fi

	say "Registering the Pi extension..."
	pi install "$pi_source"

	install_quickshell_assets

	save_state
	say "Pi Beacon is installed and running."
	say "Reload Pi with /reload, or restart Pi, to activate the extension."
	if [ -n "$QUICKSHELL_CONFIG" ]; then
		say "Instantiate PiBeaconPanel and PiBeaconServiceMenu in Quickshell config: $QUICKSHELL_CONFIG"
	fi
	say "Waybar integration: https://github.com/$REPOSITORY/tree/$VERSION/integrations/waybar"
}

tracked_quickshell_configs() {
	[ -f "$STATE_FILE" ] || return 0
	sed -n 's/^quickshell=//p' "$STATE_FILE"
}

tracked_update_check() {
	[ -f "$STATE_FILE" ] || return 0
	sed -n 's/^update_check=//p' "$STATE_FILE" | tail -n 1
}

resolve_update_check() {
	[ "$UPDATE_CHECK" -lt 0 ] || return 0
	case "$(tracked_update_check)" in
	0) UPDATE_CHECK=0 ;;
	1) UPDATE_CHECK=1 ;;
	esac
}

purge_data() {
	for tracked_config in $(tracked_quickshell_configs); do
		case "$tracked_config" in
		"." | ".." | *[!A-Za-z0-9._-]*)
			fail "Unsafe Quickshell config in installer state"
			;;
		esac
		qml_dir="$CONFIG_HOME/quickshell/$tracked_config"
		rm -f \
			"$qml_dir/PiBeaconPanel.qml" \
			"$qml_dir/PiBeaconOverview.qml" \
			"$qml_dir/PiBeaconActivity.qml" \
			"$qml_dir/PiBeaconActivityNode.qml" \
			"$qml_dir/PiBeaconModels.qml" \
			"$qml_dir/PiBeaconTheme.qml" \
			"$qml_dir/PiBeaconServiceMenu.qml"
	done
	rm -rf "$CONFIG_HOME/pi-beacon" "$CACHE_HOME/pi-beacon" "$STATE_DIR"
	if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
		rm -rf "$XDG_RUNTIME_DIR/pi-beacon"
	fi
}

uninstall_beacon() {
	[ "$(id -u)" -ne 0 ] || fail "Run this installer as your desktop user, not root"
	PATH="$HOME/.local/bin:$PATH"
	export PATH

	managed_install=0
	if [ -f "$STATE_FILE" ]; then
		managed_install=1
	fi

	if command -v systemctl >/dev/null 2>&1; then
		if [ -f "$SERVICE_FILE" ] ||
			systemctl --user is-active --quiet pi-beacon.service ||
			systemctl --user is-enabled --quiet pi-beacon.service; then
			systemctl --user disable --now pi-beacon.service ||
				fail "Could not stop and disable pi-beacon.service"
		fi
		if [ -f "$UPDATE_TIMER_FILE" ] ||
			systemctl --user is-active --quiet pi-beacon-update-check.timer ||
			systemctl --user is-enabled --quiet pi-beacon-update-check.timer; then
			systemctl --user disable --now pi-beacon-update-check.timer ||
				fail "Could not stop and disable pi-beacon-update-check.timer"
		fi
		rm -f "$SERVICE_FILE" "$UPDATE_SERVICE_FILE" "$UPDATE_TIMER_FILE"
		systemctl --user daemon-reload ||
			fail "Could not reload the systemd user manager"
	elif [ -f "$SERVICE_FILE" ] || [ -f "$UPDATE_SERVICE_FILE" ] || [ -f "$UPDATE_TIMER_FILE" ]; then
		fail "systemctl is required to remove the installed user services"
	fi

	if command -v pi >/dev/null 2>&1; then
		if ! pi remove "$PI_SOURCE_ID"; then
			[ "$managed_install" -eq 0 ] ||
				fail "Could not remove the registered Pi extension"
			say "Pi extension was not registered by this source."
		fi
	elif [ "$managed_install" -eq 1 ]; then
		fail "pi is required to remove the registered extension"
	fi

	if command -v pi-beacon >/dev/null 2>&1; then
		command -v uv >/dev/null 2>&1 ||
			fail "uv is required to remove the installed Python tool"
		uv tool uninstall pi-beacon ||
			fail "Could not remove the Pi Beacon Python tool"
	fi

	if [ "$PURGE" -eq 1 ]; then
		purge_data
		say "Pi Beacon and its local data were removed."
	else
		say "Pi Beacon was removed. Config and cache were preserved."
		say "Run uninstall --purge --yes to remove local data and installer-managed QML files."
	fi
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	install | update | uninstall)
		ACTION=$1
		ACTION_SET=1
		shift
		;;
	--yes | -y)
		ASSUME_YES=1
		shift
		;;
	--purge)
		PURGE=1
		shift
		;;
	--version)
		[ "$#" -ge 2 ] || fail "--version requires a value"
		VERSION=$2
		shift 2
		;;
	--quickshell)
		[ "$#" -ge 2 ] || fail "--quickshell requires a config name"
		QUICKSHELL_CONFIG=$2
		shift 2
		;;
	--update-check)
		UPDATE_CHECK=1
		shift
		;;
	--no-update-check)
		UPDATE_CHECK=0
		shift
		;;
	--help | -h)
		usage
		exit 0
		;;
	*)
		fail "Unknown argument: $1"
		;;
	esac
done

if [ "$ACTION_SET" -eq 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
	choose_action
fi

validate_ref
validate_quickshell_name
resolve_update_check
prepare_interactive

case "$ACTION" in
install | update)
	[ "$PURGE" -eq 0 ] || fail "--purge is valid only with uninstall"
	install_beacon
	;;
uninstall)
	[ -z "$QUICKSHELL_CONFIG" ] || fail "--quickshell is not needed during uninstall"
	uninstall_beacon
	;;
esac
