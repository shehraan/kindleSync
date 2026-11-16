#!/bin/sh
# One attempt per Linux boot. Logs/state live in /tmp except brief flag writes.
umask 077
ROOT=${KOBOSYNC_BOOT_ROOT:-}
case "$ROOT" in ''|/*) ;; *) exit 0 ;; esac
ONBOARD="$ROOT/mnt/onboard"
CONTROL="$ONBOARD/.adds/kobosync-autoboot"
READER="$ONBOARD/.adds/koreader"
TIMEOUT=${KOBOSYNC_BOOT_TIMEOUT:-90}
SETTLE=${KOBOSYNC_BOOT_SETTLE:-8}
STABLE=${KOBOSYNC_BOOT_STABLE:-60}
for value in "$TIMEOUT" "$SETTLE" "$STABLE"; do
    case "$value" in ''|*[!0-9]*) exit 0 ;; esac
done
[ "$TIMEOUT" -gt 0 ] && [ "$TIMEOUT" -le 90 ] &&
    [ "$SETTLE" -le 30 ] && [ "$STABLE" -gt 0 ] && [ "$STABLE" -le 120 ] || exit 0

# Retain this guard after exit: returning from KOReader restarts the animator,
# and must leave Nickel open rather than launching KOReader again.
mkdir -p "$ROOT/tmp" || exit 0
mkdir "$ROOT/tmp/kobosync-autoboot.attempted" 2>/dev/null || exit 0
LOG="$ROOT/tmp/kobosync-autoboot.log"
log() { printf '%s\n' "$*" >> "$LOG"; }

ready() {
    awk -v path="$ONBOARD" '
        $2 == path && $4 ~ /(^|,)rw(,|$)/ { ready = 1 }
        END { exit !ready }
    ' "$ROOT/proc/mounts" 2>/dev/null && pidof nickel >/dev/null 2>&1
}

external_power_or_usb() {
    known_status=false
    for status_file in "$ROOT"/sys/class/power_supply/*/status; do
        [ -r "$status_file" ] || continue
        IFS= read -r status < "$status_file" || return 0
        case "$status" in
            Discharging|'Not charging') known_status=true ;;
            *) return 0 ;; # Includes Full/Charging/Unknown: keep recovery available.
        esac
    done
    for type_file in "$ROOT"/sys/class/power_supply/*/type; do
        [ -r "$type_file" ] || continue
        IFS= read -r supply_type < "$type_file" || return 0
        case "$supply_type" in
            USB*|Mains|Wireless)
                online_file="${type_file%/type}/online"
                [ -r "$online_file" ] || return 0
                IFS= read -r online < "$online_file" || return 0
                [ "$online" = 0 ] || return 0
                ;;
        esac
    done
    # A bound gadget/UDC alone does not mean a cable is connected; inspect state.
    for state_file in "$ROOT"/sys/class/udc/*/state "$ROOT/sys/class/android_usb/android0/state"; do
        [ -r "$state_file" ] || continue
        IFS= read -r usb_state < "$state_file" || return 0
        case "$usb_state" in
            'not attached'|DISCONNECTED) ;;
            *) return 0 ;;
        esac
    done
    [ "$known_status" = true ] || return 0
    return 1
}

waited=0
while ! ready; do
    if [ "$waited" -ge "$TIMEOUT" ]; then
        log 'Storage/Nickel did not become ready; keeping normal Kobo startup.'
        exit 0
    fi
    sleep 1
    waited=$((waited + 1))
done
[ -f "$CONTROL/enabled" ] || exit 0
[ ! -e "$CONTROL/pending" ] || { log 'Previous startup was interrupted or failed; use Enable to retry.'; exit 0; }
awk -F, '
    { gsub(/\r/, "", $NF); model = ($NF ~ /0388$/)
      for (i = 1; i < NF; i++) if ($i == "4.38.23828") firmware = 1 }
    END { exit !(model && firmware) }
' "$ONBOARD/.kobo/version" 2>/dev/null || { log 'Device/firmware differs; keeping normal startup.'; exit 0; }
if [ ! -r "$READER/koreader.sh" ] || [ ! -r "$READER/reader.lua" ] || [ ! -x "$READER/luajit" ]; then
    log 'KOReader is incomplete; keeping normal startup.'
    exit 0
fi

# Give Nickel time to initialize hardware; check again after the delay in case
# storage was unmounted, the reader was opened manually, or a cable was attached.
sleep "$SETTLE"
ready || exit 0
[ -f "$CONTROL/enabled" ] || exit 0
if external_power_or_usb; then
    log 'External power, USB or unknown power status; keeping normal startup.'
    exit 0
fi

# An interrupted/failed start remains paused across reboots. No open descriptor
# on onboard is retained while waiting or after the short marker operations.
: > "$CONTROL/pending" || exit 0
log 'Launching KOReader once for this Linux boot.'
cd / || exit 0
/bin/sh "$READER/koreader.sh" >> "$LOG" 2>&1 &
reader_pid=$!
waited=0
while [ "$waited" -lt "$STABLE" ]; do
    sleep 1
    if ! kill -0 "$reader_pid" 2>/dev/null; then
        if wait "$reader_pid"; then
            rm -f "$CONTROL/pending"
            log 'KOReader exited cleanly; leaving normal Kobo startup available.'
        else
            log 'KOReader exited with an error; future automatic launches are paused.'
        fi
        exit 0
    fi
    waited=$((waited + 1))
done
rm -f "$CONTROL/pending"
log 'KOReader passed startup monitoring; the boot worker is exiting.'
exit 0
