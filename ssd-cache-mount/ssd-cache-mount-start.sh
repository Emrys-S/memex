#!/bin/sh
# Mounts the ssd-cache external Btrfs SSD at /volume1/ssd-cache and makes
# sure Container Manager (Docker, data-root /volume1/ssd-cache/docker) runs
# on it. Installed at /usr/local/bin/ssd-cache-mount-start.sh; run as root at
# boot from the "Goose Serve Startup" DSM task (sleep 45 && ... &).
#
# Why this is more than "mount; start":
#  - DSM regenerates /etc/fstab every boot, so the SSD is mounted by UUID here.
#  - Container Manager is an auto-start package. It starts before this script,
#    and if the SSD isn't mounted yet it bind-mounts the HDD's
#    /@syno/ssd-cache/docker over /volume1/ssd-cache/docker. Mounting the SSD
#    afterwards only hides those binds; dockerd keeps running on the HDD dir
#    and sees no containers (outages 2026-09-18 and 2026-10-10).
#  - The SSD can appear minutes or hours after boot, and DSM may auto-mount it
#    at /volumeUSB1/usbshare first.
# So: wait for the device, stop Container Manager, clear every mount at or
# under the mount point (and any /volumeUSB* mount of the SSD), mount the SSD,
# check Docker's data really is on it, and only then start Container Manager.
# Container processes can outlive `synopkg stop` (containerd-shim keeps them
# running) and each keeps a private copy of the old mount, which makes a
# re-mount of a renamed device (sdq -> sdr after a USB pull) fail with
# "File exists" (outage 2026-10-15); they are released before unmounting.
# If the check fails, Docker is left stopped and a Telegram alert is sent:
# better down than running against the wrong data-root.
#
# DRY_RUN=1 prints what it would do and changes nothing.

UUID=a839c133-2859-432a-b22e-caa6162857ff
MNT=/volume1/ssd-cache
DATA=$MNT/docker
LOG=${LOG:-/var/log/ssd-cache-mount.log}
LOCK=${LOCK:-/run/ssd-cache-mount.lock}
MIN_CONTAINERS=10          # sanity floor; there are ~30
WAIT_ALERT=600             # warn on Telegram if the SSD is missing this long
WAIT_MAX=14400             # give up waiting after 4h
NO_AUTOSTART=""            # exited containers never auto-started here (ngrok removed 2026-10-14: Cowork connector needs it)
DOCKER=/usr/local/bin/docker
COMPOSE=/usr/local/bin/docker-compose
SYNOPKG=/usr/syno/bin/synopkg
TG_TOKEN_FILE=/volume1/docker/secrets/goose-telegram-bot-token.txt
TG_CHAT_ID=157757120

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

notify() {
    [ -n "$DRY_RUN" ] && { log "(dry-run) would notify: $*"; return 0; }
    tok=$(cat "$TG_TOKEN_FILE" 2>/dev/null) || return 0
    curl -s -m 15 -X POST "https://api.telegram.org/bot${tok}/sendMessage" \
        -d chat_id="$TG_CHAT_ID" --data-urlencode "text=schoerro SSD: $*" \
        >/dev/null 2>&1 || true
}

# run a state-changing command, or just print it in dry-run mode
act() {
    if [ -n "$DRY_RUN" ]; then log "(dry-run) would run: $*"; return 0; fi
    log "run: $*"
    "$@" >>"$LOG" 2>&1
}

ssd_dev() { blkid 2>/dev/null | grep "UUID=\"$UUID\"" | cut -d: -f1 | head -1; }

# mount points at or under $MNT, deepest first
mounts_under_mnt() {
    awk -v m="$MNT" '$2 == m || index($2, m "/") == 1 { print length($2), $2 }' /proc/mounts \
        | sort -rn | cut -d' ' -f2-
}

# mount points of the SSD outside $MNT (e.g. DSM's /volumeUSB1/usbshare)
stray_ssd_mounts() {
    awk -v d="$1" -v m="$MNT" '$1 == d && $2 != m && index($2, m "/") != 1 { print $2 }' /proc/mounts
}

docker_containers() { $DOCKER info --format '{{.Containers}}' 2>/dev/null || echo 0; }

# dockerd pids, found by scanning /proc: `pidof dockerd` returns nothing for it here, which let a
# dockerd started before the SSD was mounted survive `synopkg stop` (outage 2026-10-14).
dockerd_pids() {
    for p in $(ls /proc | grep -E '^[0-9]+$'); do
        case "$(tr '\0' ' ' 2>/dev/null < /proc/$p/cmdline)" in *bin/dockerd*) echo $p;; esac
    done
}

# Container init processes (children of containerd-shim). Called only after Container Manager is
# stopped, when none should be running; every container's data-root is on this SSD, so any that
# are still alive are orphans pinning an old mount of it.
orphan_inits() {
    # one pass over /proc (this DSM's `ps` omits many processes); gives "pid ppid" lines
    tab=$(awk '/^Pid:/ {pid=$2} /^PPid:/ {print pid, $2}' /proc/[0-9]*/status 2>/dev/null)
    for p in $(ls /proc | grep -E '^[0-9]+$'); do
        case "$(tr '\0' ' ' 2>/dev/null < /proc/$p/cmdline)" in *containerd-shim-runc-v2*)
            for c in $(echo "$tab" | awk -v s="$p" '$2 == s {print $1}'); do
                echo "$c $p"
            done;;
        esac
    done
}

# SIGTERM (as `docker stop` would), then SIGKILL what is left after 40s, then the shims.
release_orphans() {
    list=$(orphan_inits)
    [ -z "$list" ] && return 0
    inits=$(echo "$list" | awk '{print $1}' | tr '\n' ' ')
    shims=$(echo "$list" | awk '{print $2}' | sort -u | tr '\n' ' ')
    n=$(echo $inits | wc -w)
    if [ -n "$DRY_RUN" ]; then
        log "(dry-run) would release $n orphaned container processes (SIGTERM, then SIGKILL after 40s)"; return 0
    fi
    log "releasing $n container processes that outlived Container Manager"
    kill -TERM $inits 2>/dev/null
    i=0
    while [ $i -lt 4 ]; do
        sleep 10; i=$((i + 1))
        left=0; for c in $inits; do [ -d /proc/$c ] && left=$((left + 1)); done
        [ "$left" -eq 0 ] && break
    done
    for c in $inits; do [ -d /proc/$c ] && kill -KILL $c 2>/dev/null; done
    sleep 3
    for sh_ in $shims; do [ -d /proc/$sh_ ] && kill -KILL $sh_ 2>/dev/null; done
    sleep 3
    notify "Released $n container processes still holding the old SSD mount, then continuing the remount."
}

# --- single instance -------------------------------------------------------
if ! mkdir "$LOCK" 2>/dev/null; then
    log "another run holds $LOCK, exiting"; exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

log "=== start${DRY_RUN:+ (DRY RUN)} ==="

# --- 0. keep the hung-task auto-reboot on --------------------------------
# A vanished USB SSD freezes the kernel (outages 2026-10-10, 10-13). These are also in
# /etc/sysctl.conf; re-applying here survives a DSM update that rewrites that file.
[ -z "$DRY_RUN" ] && sysctl -w kernel.hung_task_timeout_secs=600 kernel.hung_task_panic=1 >/dev/null 2>&1

# --- 1. wait for the SSD ---------------------------------------------------
waited=0; alerted=
DEV=$(ssd_dev)
while [ -z "$DEV" ]; do
    if [ "$waited" -ge "$WAIT_MAX" ]; then
        log "SSD $UUID not found after ${WAIT_MAX}s, giving up; Docker not touched"
        notify "SSD not detected after 4h. Docker left as is (probably on the wrong data-root). Check the cable/power, then re-run /usr/local/bin/ssd-cache-mount-start.sh"
        exit 1
    fi
    if [ -z "$alerted" ] && [ "$waited" -ge "$WAIT_ALERT" ]; then
        notify "SSD still not detected ${waited}s after boot; still waiting (up to 4h)."
        alerted=1
    fi
    sleep 15; waited=$((waited + 15))
    DEV=$(ssd_dev)
done
log "SSD is $DEV (waited ${waited}s)"

# --- 2. stop Container Manager so nothing holds the mounts ----------------
# Always stop: `synopkg status ContainerManager` reports "stop" even while
# dockerd is running (its status script errors), so it can't be trusted.
act $SYNOPKG stop ContainerManager
[ -z "$DRY_RUN" ] && sleep 5
if [ -z "$DRY_RUN" ] && [ -n "$(dockerd_pids)" ]; then
    log "dockerd survived synopkg stop, stopping its systemd unit"
    act systemctl stop pkg-ContainerManager-dockerd.service
    sleep 6
fi
if [ -z "$DRY_RUN" ] && [ -n "$(dockerd_pids)" ]; then
    log "dockerd still running after stop"
    notify "Couldn't stop Container Manager before remounting the SSD. Nothing changed; needs a look."
    exit 1
fi

# --- 2b. release container processes that outlived Container Manager -------
release_orphans

# --- 3. clear every mount at/under $MNT and any stray SSD mount -----------
for pass in 1 2 3 4 5; do
    todo="$(mounts_under_mnt) $(stray_ssd_mounts "$DEV")"
    todo=$(echo $todo)
    [ -z "$todo" ] && break
    [ -n "$DRY_RUN" ] && { log "(dry-run) would unmount (deepest first): $todo"; break; }
    for mp in $todo; do act umount "$mp" || act umount -l "$mp"; done
done
if [ -z "$DRY_RUN" ] && [ -n "$(mounts_under_mnt)" ]; then
    log "could not clear mounts: $(mounts_under_mnt | tr '\n' ' ')"
    notify "Couldn't clear old mounts under $MNT. Docker left stopped; needs a look."
    exit 1
fi

# --- 4. mount the SSD ------------------------------------------------------
mkdir -p "$MNT"
act mount -t btrfs "$DEV" "$MNT"

# --- 5. verify Docker's data really is on the SSD -------------------------
if [ -z "$DRY_RUN" ]; then
    d_mnt=$(stat -c %d "$MNT" 2>/dev/null); d_data=$(stat -c %d "$DATA/containers" 2>/dev/null)
    d_hdd=$(stat -c %d /volume1 2>/dev/null)
    n=$(ls "$DATA/containers" 2>/dev/null | wc -l)
    log "check: dev(mnt)=$d_mnt dev(data)=$d_data dev(/volume1)=$d_hdd containers=$n"
    if [ -z "$d_data" ] || [ "$d_data" != "$d_mnt" ] || [ "$d_data" = "$d_hdd" ] \
       || [ "$n" -lt "$MIN_CONTAINERS" ]; then
        notify "Docker data not found on the SSD after mounting (containers=$n). Container Manager left STOPPED to avoid the wrong data-root."
        exit 1
    fi
fi

# --- 6. start Container Manager and confirm --------------------------------
act $SYNOPKG start ContainerManager
if [ -z "$DRY_RUN" ]; then
    i=0; c=0
    while [ $i -lt 36 ]; do
        c=$(docker_containers); [ "$c" -ge "$MIN_CONTAINERS" ] 2>/dev/null && break
        sleep 5; i=$((i + 1))
    done
    if [ "$c" -lt "$MIN_CONTAINERS" ] 2>/dev/null; then
        notify "Container Manager started but Docker only sees $c containers. Needs a look."
        exit 1
    fi
    log "Docker sees $c containers"
    sleep 60   # let restart policies bring things up first
fi

# --- 7. start containers a shutdown left exited ----------------------------
# Container Manager's own stop uses docker stop, which "unless-stopped"
# treats as a manual stop, so those stay down after the next start.
for name in $($DOCKER ps -a --filter status=exited --format '{{.Names}}' 2>/dev/null); do
    case " $NO_AUTOSTART " in *" $name "*) continue;; esac
    pol=$($DOCKER inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$name" 2>/dev/null)
    case "$pol" in unless-stopped|always) act $DOCKER start "$name";; esac
done

# --- 8. qbittorrent shares gluetun's network namespace; recreate if stale --
if [ -z "$DRY_RUN" ]; then
    sleep 30
    if ! $DOCKER exec gluetun wget -qO- --timeout=10 http://localhost:8090/api/v2/app/version >/dev/null 2>&1; then
        log "qbittorrent unreachable via gluetun, recreating"
        (cd /volume1/docker/projects/vpnproject-compose && act $COMPOSE -p vpn-project up -d qbittorrent)
    fi
fi

running=$($DOCKER ps -q 2>/dev/null | wc -l)
log "=== done: $running containers running ==="
[ -n "$waited" ] && [ "$waited" -gt 0 ] && notify "SSD came up ${waited}s after boot; Docker started on it ($running running)."
exit 0
