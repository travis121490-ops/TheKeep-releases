"""Deployment glue only: no application source execution, credentials or household state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

OWNER = "travis121490-ops"
SOURCE = f"{OWNER}/TheKeep"
FEED = f"https://{OWNER}.github.io/TheKeep-releases"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_run(run, jobs):
    require(run["repository"]["full_name"] == SOURCE, "Wrong source repository")
    require(run["event"] == "workflow_dispatch" and run["head_branch"] == "main", "Not deliberate main release")
    require(run["path"] == ".github/workflows/release-the-keep.yml", "Wrong source workflow")
    require(run["actor"]["login"] == OWNER and run["triggering_actor"]["login"] == OWNER, "Owner invocation required")
    require(re.fullmatch(r"[0-9a-f]{40}", run["head_sha"]), "Invalid source SHA")
    prepared = [job for job in jobs["jobs"] if job["name"] == "release"]
    require(len(prepared) == 1 and prepared[0]["conclusion"] == "success", "Source preparation has not passed")


def validate_channel(run, incoming, draft, out):
    incoming, out = Path(incoming), Path(out)
    require(not out.exists(), "Preserve existing output")
    require({p.name for p in incoming.iterdir()} == {"channel.zip", "release-evidence.json"}, "Unexpected incoming artifact")
    evidence = json.loads((incoming / "release-evidence.json").read_text(encoding="utf-8-sig"))
    require(evidence["mode"] == "production" and evidence["official"] is True, "Test artifacts cannot publish")
    require(evidence["source"] == run["head_sha"] and str(evidence["run"]) == str(run["id"]), "Source/run mismatch")
    require(evidence["feed"].rstrip("/") == FEED, "Wrong channel feed")
    code = evidence["identity"]["code"]
    require(type(code) is int and code > 0 and evidence["identity"]["packageId"] == "app.thekeep.keep", "Wrong release identity")
    require(draft["draft"] is True and not draft["prerelease"] and draft["tag_name"] == f"keep-{code}", "No unfinished official draft")
    require(draft["target_commitish"] == run["head_sha"], "Draft source mismatch")
    files = evidence["files"]
    expected = {f"{directory}/{name}" for directory in ("windows", "android/app.thekeep.keep")
                for name in ("release.manifest", "release.sig", f"{code}.keeprelease")}
    require(len(files) == 6 and {row["path"] for row in files} == expected, "Incomplete current signed files")
    with zipfile.ZipFile(incoming / "channel.zip") as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(len(names) == len(set(names)), "Duplicate archive paths")
        require(sum(entry.file_size for entry in entries) <= 1_000_000_000, "Channel exceeds Pages capacity; reconcile retention")
        for entry in entries:
            name = entry.filename
            allowed = name == ".nojekyll" or name in expected
            match = re.fullmatch(r"(?:windows|android/app\.thekeep\.keep)/([1-9][0-9]*)\.keeprelease", name)
            allowed = allowed or (match is not None and int(match[1]) < code)
            require(allowed and not entry.is_dir() and (entry.external_attr >> 16) & 0o170000 != 0o120000, "Unexpected static path")
        require(expected <= set(names) and ".nojekyll" in names, "Missing static channel files")
        require(archive.read(".nojekyll") == b"", "Unexpected static marker")
        for row in files:
            payload = archive.read(row["path"])
            require(len(payload) == row["size"] and hashlib.sha256(payload).hexdigest() == row["sha256"], "Final bytes differ from source evidence")
        out.mkdir()
        # Paths were completely whitelisted above, before any write.
        archive.extractall(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("run", "jobs", "incoming", "draft", "out"):
        parser.add_argument(f"--{name}", required=name in ("run", "jobs"))
    args = parser.parse_args()
    run = json.loads(Path(args.run).read_text())
    validate_run(run, json.loads(Path(args.jobs).read_text()))
    if args.incoming:
        validate_channel(run, args.incoming, json.loads(Path(args.draft).read_text()), args.out)
    print("Verified owner source preparation" + (" and static channel bytes" if args.incoming else ""))
