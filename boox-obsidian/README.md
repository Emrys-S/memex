# boox-obsidian: T: tasks <-> Obsidian task list <-> Marvin (built 2026-10-15)

The canonical `boox_to_obsidian.py` lives on the NAS (`/volume1/docker/boox-obsidian/`, baked into the
`boox-watcher` image); this folder keeps the tests and notes. Backups of the pre-change script and compose:
`boox_to_obsidian.py.bak-20261015-pre-tasks`, `docker-compose.yml.bak-20261015-pre-tasks`.

**What a `T:` task now does**
1. Creates a Marvin task (`addTask`; the response `_id` is kept).
2. Appends a Tasks-plugin line to `Projects/Tasks/Tasks.md`:
   `- [ ] text ([[YYYY-MM-DD]] · [[source note]]) [marvin:ID] ➕ YYYY-MM-DD`
   (links to that day's daily note, created if missing, and to the source note).
3. The source note keeps a plain `- 📌 text` bullet instead of a checkbox, so the daily "Open Tasks" query
   shows each task once. If the task list file is missing, the old `- [ ]` behaviour is used.
4. Idempotent: the same task text from the same source note is not captured twice (reprocessing no longer
   duplicates Marvin tasks either).

**Completion sync** (`sync_tasks()`, every `TASKS_SYNC_INTERVAL`=600 s, only for lines carrying `[marvin:ID]`)
- Ticked in Obsidian -> `markDone` in Marvin (once; state in `/data/tasks_sync.json`; gives up after 3 failures).
- Done in Marvin -> ticked in Obsidian with a `✅` date: **only if `MARVIN_FULL_TOKEN` is set**. The regular API
  token cannot read one task's state (`GET /api/doc` needs the Full Access Token), and the inbox listing drops a
  task whether it was completed, moved or deleted. Off by default (least privilege).
- Nothing else (text, due dates, priority) is synced.

**Mounts**: the container sees only `Projects/Tasks` of the vault for this (added 2026-10-15).
**Tests**: `docker run --rm -v $PWD/boox_to_obsidian.py:/app/boox_to_obsidian.py:ro -v $PWD/test_tasks.py:/app/test_tasks.py:ro -w /app boox-obsidian-boox-watcher python3 -m unittest -v test_tasks` (9 tests).
**To enable Marvin -> Obsidian**: `set-secret.sh /volume1/docker/boox-obsidian MARVIN_FULL_TOKEN` (Marvin Settings -> API -> Full Access Token), then redeploy with `sops-deploy.sh`.
