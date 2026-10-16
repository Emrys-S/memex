#!/bin/bash
# nas-recorder.sh -- one-minute flight recorder for the NAS (added 2026-10-16).
#
# Why: the NAS has twice stalled for ~an hour (ping and TCP connect worked, SSH / DSM / Tailscale did not), then
# recovered, and the kernel log held nothing from the stall, so there was no evidence afterwards. This writes one
# greppable line per minute to /var/log/nas-recorder.log from a stateless script run by a DSM Task Scheduler task
# (id 14, every minute). Reading it after the next stall answers the key question:
#   * ticks KEEP coming with network checks failing  -> the NAS is alive, the network path is the problem
#   * ticks STOP (a "gap=" jump) or "dur=" balloons   -> the NAS itself (userland, disk) was frozen
#   * dstate=N with names                             -> processes blocked in uninterruptible I/O wait
# Every external check is wrapped in `timeout` so one stuck check cannot hang the tick.
#
# Also keeps Energy Efficient Ethernet off on eth0: DSM turns it back on at every boot, and EEE is a known cause of
# odd packet loss with some switches (the setting is applied within a minute of boot; a line says so).

LOG=/var/log/nas-recorder.log
LAST=/run/nas-recorder.last
MAX_BYTES=4000000
IFACE=eth0
DOCKER=/usr/local/bin/docker

now=$(date +%s)
iso=$(date '+%Y-%m-%dT%H:%M:%S%z')
[ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_BYTES" ] && mv -f "$LOG" "$LOG.1"

prev=$(cat "$LAST" 2>/dev/null || echo 0)
gap=$([ "$prev" -gt 0 ] && echo $((now - prev)) || echo "-")
echo "$now" > "$LAST"

up=$(cut -d. -f1 /proc/uptime)
load=$(cut -d' ' -f1-3 /proc/loadavg)
mem=$(awk '/^MemAvailable/ {a=$2} /^SwapTotal/ {st=$2} /^SwapFree/ {sf=$2} END {printf "avail=%dM swapused=%dM", a/1024, (st-sf)/1024}' /proc/meminfo)

# processes in uninterruptible sleep (D): count and the first few names. Read /proc directly: this DSM's ps omits many.
dstate=$(awk 'FNR==1 { if (match($0, /\) [A-Z] /)) { s = substr($0, RSTART + 2, 1); if (s == "D") { c = $0; sub(/^[0-9]+ \(/, "", c); sub(/\) [A-Z] .*$/, "", c); if (c !~ /^(md[0-9]+_|jbd2|kworker|flush-|synoschedtask|nas-recorder)/) { n++; if (k++ < 5) names = names c "," } } } }
                END { printf "%d%s", n + 0, (names != "" ? "(" names ")" : "") }' /proc/[0-9]*/stat 2>/dev/null)

[ -z "$dstate" ] && dstate=?      # awk lost a race with an exiting process; say so rather than leave it blank

st() { cat /sys/class/net/$IFACE/statistics/$1 2>/dev/null || echo 0; }
net="rxB=$(st rx_bytes) rxP=$(st rx_packets) rxE=$(st rx_errors) rxD=$(st rx_dropped) txB=$(st tx_bytes) txP=$(st tx_packets) txE=$(st tx_errors) txD=$(st tx_dropped)"
# resource exhaustion signatures: connection-tracking table, sockets, file handles, ephemeral ports
ctn=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo -); ctm=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo -)
socks=$(awk '/^TCP:/ {printf "inuse=%s orphan=%s tw=%s alloc=%s", $3, $5, $7, $9}' /proc/net/sockstat 2>/dev/null)
fds=$(awk '{print $1 "/" $3}' /proc/sys/fs/file-nr 2>/dev/null)
res="ct=$ctn/$ctm tcp[$socks] fds=$fds"
link="$(cat /sys/class/net/$IFACE/operstate 2>/dev/null)/$(cat /sys/class/net/$IFACE/speed 2>/dev/null)"

# EEE: keep it off (see header)
eee=off
if ethtool --show-eee "$IFACE" 2>/dev/null | grep -q "EEE status: enabled"; then
    if timeout 10 ethtool --set-eee "$IFACE" eee off >/dev/null 2>&1; then eee=turned-off-now; else eee=still-on; fi
fi

pingms() { # host -> "12.3ms" | FAIL
    local out; out=$(timeout 4 ping -c1 -W2 "$1" 2>/dev/null | sed -n 's/.*time=\([0-9.]*\).*/\1/p')
    [ -n "$out" ] && echo "${out}ms" || echo FAIL
}
gw=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')
gwp=$(pingms "${gw:-192.168.1.1}")
neigh=$(ip neigh show "$gw" 2>/dev/null | awk '{print $NF; exit}')
inet=$(pingms 1.1.1.1)
dns=$(timeout 5 nslookup mailgun.org >/dev/null 2>&1 && echo ok || echo FAIL)

# do the local daemons answer?
sshd=$(timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/22 && head -c 4 <&3' 2>/dev/null | grep -q SSH && echo ok || echo FAIL)
dsm=$(timeout 5 curl -s -o /dev/null -m 4 -w '%{http_code}' http://127.0.0.1:5000/ 2>/dev/null); [ -z "$dsm" ] && dsm=FAIL
dk_out=$(timeout 8 "$DOCKER" ps -q 2>/dev/null); dk_rc=$?
if [ $dk_rc -eq 0 ]; then dk=$(printf '%s\n' "$dk_out" | grep -c .); else dk=FAIL; fi   # a hung docker must read FAIL, not 0
ts=$(timeout 8 "$DOCKER" exec tailscale tailscale status --json 2>/dev/null | grep -o '"BackendState": *"[A-Za-z]*"' | head -1 | sed 's/.*: *"//; s/"//')
[ -z "$ts" ] && ts=FAIL

dur=$(( $(date +%s) - now ))
boot=$([ "$up" -lt 180 ] && echo " BOOT" || echo "")
echo "$iso up=$up gap=$gap dur=$dur load=\"$load\" mem=\"$mem\" dstate=$dstate link=$link eee=$eee gw=$gwp neigh=${neigh:--} inet=$inet dns=$dns sshd=$sshd dsm=$dsm docker=$dk ts=$ts $res $net$boot" >> "$LOG"
