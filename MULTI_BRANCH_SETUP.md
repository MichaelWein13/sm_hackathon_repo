# Working Across Branches Simultaneously

Each person's code lives on a separate branch. Use **git worktrees** to check out every branch in its own folder — no branch switching, no merge conflicts while developing.

## All branches

| Person | Role | Branch | Worktree folder |
|--------|------|--------|-----------------|
| 1 | Signal standardization | `main` | `sm_hackathon-person1/` |
| 2 | Zone discovery | `person-2-zone-discovery` | `sm_hackathon-person2/` |
| 3 | Movement graph | `person_3_movement` | `sm_hackathon-person3/` |
| 4 | Insight engine | `part4_iape` | `sm_hackathon_repo/` |
| 5 | Visualization | `shay` | `sm_hackathon-person5/` |

Replace `sm_hackathon_repo/` with your own folder name — that's where **your** branch lives and where you commit.

## One-time setup

```bash
# 1. Clone the repo and check out YOUR branch
git clone <repo-url> sm_hackathon_repo
cd sm_hackathon_repo
git checkout <your-branch>     # e.g. part4_iape, shay, person_3_movement, …

# 2. Fetch all remote branches
git fetch origin

# 3. Add worktrees for every OTHER branch (run from sm_hackathon_repo/)
git worktree add ../sm_hackathon-person1 origin/main
git worktree add ../sm_hackathon-person2 origin/person-2-zone-discovery
git worktree add ../sm_hackathon-person3 origin/person_3_movement
git worktree add ../sm_hackathon-person5 origin/shay
# skip the worktree for your own branch — you already have it

# 4. Shared folder for pipeline I/O (outside any branch)
mkdir -p ../floorflow-io/movement_graphs ../floorflow-io/anomaly_reports
```

Resulting layout:

```
cs_hackathon/
├── sm_hackathon_repo/       ← YOUR branch (where you commit)
├── sm_hackathon-person1/    ← Person 1 — signal standardization (main)
├── sm_hackathon-person2/    ← Person 2 — zone discovery
├── sm_hackathon-person3/    ← Person 3 — movement graph
├── sm_hackathon-person5/    ← Person 5 — visualization (shay)
└── floorflow-io/            ← shared runtime data
    ├── movement_graphs/     ← Person 3 writes, Person 4 reads
    └── anomaly_reports/     ← Person 4 writes, Person 5 reads
```

## Open everything in Cursor / VS Code

The workspace file lives **in the repo root**: `floorflow.code-workspace`

```bash
cd sm_hackathon_repo
cursor floorflow.code-workspace
# or: File → Open Workspace from File… → floorflow.code-workspace
```

All folders appear in the sidebar at once.

## Wire-up by role

| Person | Writes to | Reads from |
|--------|-----------|------------|
| 1 — Signals | standardized JSON (feeds Person 2) | raw BLE / Wi-Fi data |
| 2 — Zones | zone assignments (feeds Person 3) | Person 1 output |
| 3 — Movement graph | `floorflow-io/movement_graphs/graph_<ts>.json` | zone data from Person 2 |
| 4 — Insight engine | `floorflow-io/anomaly_reports/` | `floorflow-io/movement_graphs/` |
| 5 — Visualization | — | `floorflow-io/anomaly_reports/` + movement graphs |

**Person 4 example:**

```bash
cd sm_hackathon_repo/part_4
python3 insight_engine/engine.py \
  --graph-dir ../../floorflow-io/movement_graphs \
  --out-dir ../../floorflow-io/anomaly_reports
```

Point your own code at the same paths via config or CLI flags — the folders are shared on disk, so no branch merging is needed for integration testing.

## Day-to-day workflow

```bash
# Pull latest on YOUR branch
cd sm_hackathon_repo && git pull

# Refresh a teammate's worktree
cd ../sm_hackathon-person3 && git pull

# See all worktrees
cd sm_hackathon_repo && git worktree list
```

**Rule:** only commit from your own folder (`sm_hackathon_repo/`). Other worktrees are for reading and running.

## Cleanup

```bash
cd sm_hackathon_repo
git worktree remove ../sm_hackathon-person1
git worktree remove ../sm_hackathon-person2
git worktree remove ../sm_hackathon-person3
git worktree remove ../sm_hackathon-person5
```

## New branches

When someone pushes a new branch:

```bash
git fetch origin
git worktree add ../sm_hackathon-<name> origin/<branch-name>
```

Then add the folder to `floorflow.code-workspace` in the repo root.
