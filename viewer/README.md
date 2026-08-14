# Lineage reader and viewer

`lineage_reader.py` is the read-only interpretation layer for an ouroboros
lineage. `server.py` is an optional browser over that reader. Both understand
the complete timeline, generation diffs, matching journal entries, state
changes, rejected successors, and the committed boot source behind each death.
Out-of-loop context and `external edit` commits remain visible but are not
assigned generation numbers.

Try it with the frozen fixture:

```sh
git clone viewer/testdata/sample-lineage.bundle /tmp/sample
python3 viewer/lineage_reader.py /tmp/sample summary
python3 viewer/lineage_reader.py /tmp/sample generation 5
python3 viewer/lineage_reader.py /tmp/sample compare 1 5
python3 viewer/lineage_reader.py /tmp/sample verify
python3 viewer/server.py /tmp/sample
```

Then open <http://127.0.0.1:8765>. For an actively running lineage, the viewer
looks first for the separate git directory at `<worktree>.git`; override it when
needed:

```sh
python3 viewer/server.py /path/to/worktree --git-dir /path/to/lineage.git
```

The reader and server do not write to the worktree or git directory. Their
shared Git wrapper sets `GIT_OPTIONAL_LOCKS=0`, disables external diff helpers,
supplies `--git-dir` and `--work-tree` explicitly, and rejects commands outside
the read-only `log`, `show`, `diff`, `cat-file`, and `rev-list` allowlist.

Run the fixture-backed tests with:

```sh
python3 -m unittest discover -s viewer -p 'test_*.py'
```
