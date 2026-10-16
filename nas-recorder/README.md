# nas-recorder: flight recorder for NAS stalls (2026-10-16)

The NAS stalled twice in a day for about an hour each (ping and the TCP handshake worked; SSH, DSM and Tailscale did
not), recovered or was reset, and left no evidence: the kernel log held nothing from the stall. This records one line a
minute so the next stall explains itself.

**On the NAS**: `nas-recorder.sh` installed at `/usr/local/bin/nas-recorder.sh`, run every minute by DSM Task Scheduler
task id 14 ("NAS flight recorder", a hand-written `.task` file copied from the hourly task id 12, then
`systemctl restart synoscheduled`). Log: `/var/log/nas-recorder.log` (rotated at 4 MB). Each line has: uptime, `gap=` (seconds
since the previous tick), `dur=` (how long this tick took), load, memory/swap, processes stuck in uninterruptible I/O
(`dstate=`, md RAID and kernel threads ignored), link state and speed, `eee=`, ping to the gateway and the internet, gateway
ARP state, DNS, whether sshd / the DSM web server / Docker answer *locally*, Tailscale's state, and the NIC counters.

How to read it after a stall (compare the times with the Mac probe below):
- ticks keep coming with `gw=FAIL` / `inet=FAIL` / `neigh=FAILED` -> the NAS was alive, the network path was the problem
- ticks stop, or `gap=` jumps, or `dur=` balloons -> the NAS itself (userland or disk) froze
- `dstate=` with names -> what was blocked on I/O
- `sshd=FAIL` or `dsm=FAIL` while ping works -> those daemons hung, not the network

**EEE**: Energy Efficient Ethernet on the NAS's igb NIC is a known cause of odd packet loss with some switches. DSM
re-enables it at every boot, so the recorder turns it off whenever it finds it enabled (`eee=turned-off-now` in the log);
it is therefore back off within a minute of any boot. Turning it off renegotiates the link, so that one tick shows
failed network checks.

**On the Mac** (not in this repo: it holds the LAN address and the Funnel hostname): `~/Library/Application Support/nas-probe/probe.sh`,
run every 60 s by the launchd agent `~/Library/LaunchAgents/com.emrys.nas-probe.plist`, logging to `~/Library/Logs/nas-probe.log`.
Per minute it tests each layer separately (small ping, full-size 1500-byte ping, TCP 22, the SSH greeting, DSM web); every
5 minutes a real SSH login (which also returns uptime and load) and the two public Funnel ports; and on a failure it saves a
diagnostic burst (`nas-probe-burst-*.log`: ARP, pings, `ssh -vvv`, `curl -v`) at most every 10 minutes. It marks `home=no` and
skips the LAN checks when the Mac is not on 192.168.1.x. Remove with `launchctl bootout gui/$(id -u)/com.emrys.nas-probe`.
The logs use each machine's own time zone (Mac -0400, NAS +0200); the offsets are in every line.

Known limit to verify: task 14 is set to repeat every minute with `last work hour=23`; if DSM stops at 23:00 rather than
23:59, the recorder will show a gap every night from 23:00 to 00:00.
