#!/bin/sh
# Run from the NickelMenu drop-in; changes only this component and its boot hook.
set -eu
umask 022

ROOT=${KOBOSYNC_BOOT_ROOT:-}
case "$ROOT" in ''|/*) ;; *) echo 'Filesystem root must be absolute' >&2; exit 1 ;; esac
ONBOARD="$ROOT/mnt/onboard"
CONTROL="$ONBOARD/.adds/kobosync-autoboot"
INSTALLED="$ROOT/usr/local/kobosync-autoboot"
ANIMATOR="$ROOT/etc/init.d/on-animator.sh"
ORIGINAL="$INSTALLED/on-animator.original"
ORIGINAL_DIGEST="$INSTALLED/original.md5"
EXPECTED="$INSTALLED/on-animator.installed"
SOURCE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

fail() { printf '%s\n' "$*" >&2; exit 1; }

digest() {
    output=$(md5sum "$1") || return 1
    printf '%s\n' "${output%% *}"
}

verify_original() {
    [ -f "$ORIGINAL_DIGEST" ] || fail 'Original startup checksum is missing; autostart stays disabled'
    IFS= read -r saved_digest < "$ORIGINAL_DIGEST"
    [ "$(digest "$ORIGINAL")" = "$saved_digest" ] || fail 'Original startup backup changed; refusing to restore it'
}

case "${1:-}" in
    enable|disable|status) [ "$#" -eq 1 ] || fail 'Use enable, disable or status' ;;
    *) fail 'Use enable, disable or status' ;;
esac

if [ "$1" = status ]; then
    if [ ! -f "$CONTROL/enabled" ]; then
        echo 'Automatic KOReader startup is disabled.'
    elif [ -e "$CONTROL/pending" ]; then
        echo 'Automatic startup is paused after an interrupted/failed start. Use Enable to retry.'
    elif [ -f "$EXPECTED" ] && cmp -s "$ANIMATOR" "$EXPECTED"; then
        echo 'Automatic KOReader startup is enabled. Boot without a USB/power cable.'
    else
        echo 'Automatic startup needs installation or repair. Use Enable.'
    fi
    exit 0
fi

[ -n "$ROOT" ] || [ "$(id -u)" -eq 0 ] || fail 'Run this action from NickelMenu on the Kobo'

animation_tmp="$ANIMATOR.kobosync-$$.tmp"
asset_tmp="$INSTALLED/autostart-$$.tmp"
enabled_tmp="$CONTROL/enabled-$$.tmp"
expected_tmp="$INSTALLED/installed-$$.tmp"
trap 'rm -f "$animation_tmp" "$asset_tmp" "$enabled_tmp" "$expected_tmp"' 0
trap 'exit 1' 1 2 15

if [ "$1" = disable ]; then
    # Deactivate first, including when another mod subsequently changed the hook.
    rm -f "$CONTROL/enabled"
    if [ -f "$EXPECTED" ] && [ -f "$ORIGINAL" ] && cmp -s "$ANIMATOR" "$EXPECTED"; then
        verify_original
        cp -p "$ORIGINAL" "$animation_tmp"
        cmp -s "$ORIGINAL" "$animation_tmp" || fail 'Original startup copy did not verify'
        mv -f "$animation_tmp" "$ANIMATOR"
        echo 'Disabled; the original Kobo animation script was restored.'
    else
        echo 'Disabled; the current animation script was left unchanged.'
    fi
    exit 0
fi

# The model and exact firmware must match the inspected device. Check on every
# enable, not only while copying files to onboard storage.
awk -F, '
    { gsub(/\r/, "", $NF); model = ($NF ~ /0388$/)
      for (i = 1; i < NF; i++) if ($i == "4.38.23828") firmware = 1 }
    END { exit !(model && firmware) }
' "$ONBOARD/.kobo/version" || fail 'Supported device: Libra 2, firmware 4.38.23828 only'
if [ ! -f "$ANIMATOR" ] || [ -L "$ANIMATOR" ]; then
    fail 'Expected a regular Kobo animation script'
fi
IFS= read -r first_line < "$ANIMATOR"
[ "$first_line" = '#!/bin/sh' ] || fail 'Unexpected animation script interpreter'
[ -r "$SOURCE/autostart.sh" ] || fail 'The startup launcher is missing'
/bin/sh -n "$SOURCE/autostart.sh" || fail 'The startup launcher failed its syntax check'
if [ ! -r "$ONBOARD/.adds/koreader/koreader.sh" ] ||
    [ ! -r "$ONBOARD/.adds/koreader/reader.lua" ] ||
    [ ! -x "$ONBOARD/.adds/koreader/luajit" ]; then
    fail 'The KOReader installation is incomplete'
fi

if [ -e "$ORIGINAL" ] || [ -e "$EXPECTED" ]; then
    if [ ! -f "$ORIGINAL" ] || [ ! -f "$EXPECTED" ]; then
        fail 'Incomplete previous installation; keep autostart disabled'
    fi
    verify_original
    if ! cmp -s "$ANIMATOR" "$EXPECTED" && ! cmp -s "$ANIMATOR" "$ORIGINAL"; then
        fail 'Startup changed since installation; preserve those changes and keep autostart disabled'
    fi
else
    if grep -qF '# BEGIN KoboSync automatic startup' "$ANIMATOR"; then
        fail 'An existing KoboSync hook has no matching backup; keep autostart disabled'
    fi
    mkdir -p "$INSTALLED"
    cp -p "$ANIMATOR" "$ORIGINAL"
    cmp -s "$ANIMATOR" "$ORIGINAL" || fail 'Original startup backup did not verify'
    digest "$ORIGINAL" > "$ORIGINAL_DIGEST"
fi

# Build from the saved original, preserving its mode and unmodified body.
# Keep the original pathname/process name: Nickel kills on-animator.sh by name.
cp -p "$ORIGINAL" "$animation_tmp"
awk 'NR == 1 {
    print
    print "# BEGIN KoboSync automatic startup"
    print "/bin/sh /usr/local/kobosync-autoboot/autostart.sh </dev/null >/dev/null 2>&1 &"
    print "# END KoboSync automatic startup"
    next
} { print }' "$ORIGINAL" > "$animation_tmp"
/bin/sh -n "$animation_tmp" || fail 'Generated startup script failed its syntax check'
cp "$SOURCE/autostart.sh" "$asset_tmp"
chmod 755 "$asset_tmp"
cmp -s "$SOURCE/autostart.sh" "$asset_tmp" || fail 'Startup launcher copy did not verify'
cp -p "$animation_tmp" "$expected_tmp"
mkdir -p "$CONTROL"
printf '%s\n' '0.1.0' > "$enabled_tmp"

mv -f "$asset_tmp" "$INSTALLED/autostart.sh"
mv -f "$expected_tmp" "$EXPECTED"
mv -f "$animation_tmp" "$ANIMATOR"
cmp -s "$ANIMATOR" "$EXPECTED" || fail 'Installed startup script did not verify'
rm -f "$CONTROL/pending"
mv -f "$enabled_tmp" "$CONTROL/enabled"
echo 'Enabled. Restart the Kobo without a USB/power cable to open KOReader automatically.'
echo 'To return to normal startup, use Disable or delete .adds/kobosync-autoboot/enabled over USB.'
