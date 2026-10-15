# merge-review

Duplicate-person review queue for the Obsidian vault. Deployed on the NAS at
`/volume1/docker/merge-review/` (container `merge-review`, no network, read-only root fs, only the
vault folders it needs). Canonical copy is on the NAS; this folder is the versioned copy + tests.

- `merge_review.py` -- sweep -> `Review/Merge queue.md` (checkbox items) -> carries out ticked options.
- `test_merge_review.py` -- 43 checks on a throwaway vault: `python3 test_merge_review.py`.
- `docker-compose.yml` -- the deployed service definition.

Safety: nothing happens without a ticked box; one tick per item; every merge/rename backs up the source,
target and every rewritten note to `data/backups/` first and restores them if a step fails; source notes are
moved to `Review/merged/`, never deleted; code spans/fences, `Personal/`, `.Trash`, `Apple notes` are never
touched; bare first-name notes are only ever listed, never auto-merged. Undo = copy files back from the backup
dir (relative paths mirror the vault) and move the note back from `Review/merged/`.
