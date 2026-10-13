"""Deployment glue only: no application source execution, credentials or household state."""
import argparse
import hashlib
import io
import subprocess
from datetime import datetime, timezone
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


def select_draft(pages, code):
    matches = [release for page in pages for release in page if release["tag_name"] == f"keep-{code}"]
    require(len(matches) == 1, "Missing or ambiguous staged release")
    draft = matches[0]
    require(type(draft.get("id")) is int and draft["id"] > 0 and draft["draft"] is True and not draft["prerelease"], "Unfinished numeric draft required")
    return draft


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


BANKED_SOURCE = "91c2b1d7426fc5672012228cec6ca25d413017c7"
BANKED_RUN = 38321857517
BANKED_ARTIFACT = 11779609187
BANKED_DIGEST = "a994f3ef79e51e683e57200a8070fe01959238a719ea3d58c98a86cf753914c0"
BANKED_ASSETS = {
    "channel.zip": (635321812, 188441024, "189fea524c5bee1b48084762362be2bf9b799527b96e33b7f1b84a9c9b9a327d"),
    "release-evidence.json": (635321813, 3271, "65d15b3e0cab35a0192de60111248a15127736acd31cc81cf756a758a4efe741"),
    "trusted-bootstrap.zip": (635321816, 41596, "9e4d682d60f23737c3e36bd76239c083ec1d19034dd04dc2f34aba620cab2914"),
}


def validate_archive(metadata, payload, artifact_id, digest, name, run_id, source):
    require(re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid dispatched archive digest")
    require(metadata["id"] == artifact_id and metadata["name"] == name and metadata["expired"] is False,
            "Wrong or expired immutable artifact")
    require(datetime.fromisoformat(metadata["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc),
            "Expired artifact retention")
    producer = metadata["workflow_run"]
    require(producer["id"] == run_id and producer["head_sha"] == source and producer["head_branch"] == "main",
            "Artifact producer mismatch")
    require(metadata["digest"] == "sha256:" + digest and hashlib.sha256(payload).hexdigest() == digest,
            "Artifact archive digest mismatch")


def validate_receipt(receipt, recovery, jobs, source_run, source_jobs, recovery_id, release_id):
    validate_run(source_run, source_jobs)
    require(source_run["id"] == BANKED_RUN and source_run["head_sha"] == BANKED_SOURCE and
            source_run["run_attempt"] == 1 and source_run["status"] == "completed", "Original producer changed")
    require(recovery["repository"]["full_name"] == SOURCE and recovery["id"] == recovery_id and
            recovery["path"] == ".github/workflows/resume-1301-publication.yml" and
            recovery["event"] == "workflow_dispatch" and recovery["head_branch"] == "main" and
            recovery["actor"]["login"] == OWNER and recovery["triggering_actor"]["login"] == OWNER and
            recovery["run_attempt"] == 1 and recovery["status"] == "in_progress" and
            re.fullmatch(r"[0-9a-f]{40}", recovery["head_sha"]), "Untrusted or stale recovery dispatch")
    producers = [job for job in jobs["jobs"] if job["name"] == "publish-only"]
    require(len(producers) == 1 and producers[0]["run_id"] == recovery_id and producers[0]["run_attempt"] == 1,
            "Ambiguous receipt producer job")
    for name in ("Validate banked 1301 receipt", "Seal validated draft receipt"):
        steps = [step for step in producers[0]["steps"] if step["name"] == name]
        require(len(steps) == 1 and steps[0]["status"] == "completed" and steps[0]["conclusion"] == "success",
                "Receipt producer validation/upload incomplete")
    expected = dict(format=1, source=BANKED_SOURCE, producerRun=BANKED_RUN, producerAttempt=1,
                    releaseId=411083848, recoveryRun=recovery_id, recoveryAttempt=1,
                    recoverySource=recovery["head_sha"], channelArtifactId=BANKED_ARTIFACT,
                    channelArtifactDigest="sha256:" + BANKED_DIGEST)
    require(release_id == 411083848 and all(receipt.get(k) == v for k, v in expected.items()), "Receipt identity mismatch")
    draft = receipt["draft"]
    require(draft["id"] == release_id and draft["draft"] is True and draft["prerelease"] is False and
            draft["tag_name"] == "keep-1301" and draft["target_commitish"] == BANKED_SOURCE, "Receipt draft mismatch")
    require(len(draft["assets"]) == 3 and {a["name"] for a in draft["assets"]} == set(BANKED_ASSETS), "Draft asset substitution")
    for asset in draft["assets"]:
        asset_id, size, digest = BANKED_ASSETS[asset["name"]]
        require((asset["id"], asset["size"], asset["digest"]) == (asset_id, size, "sha256:" + digest),
                "Draft asset identity/digest mismatch")
    return draft


def consume_handoff(source_run, source_jobs, recovery_id, artifact_id, digest, release_id, incoming, out, api=None):
    # Only Actions endpoints: the restricted channel credential never reads a private Release.
    if api is None:
        def api(path):
            return subprocess.run(["gh", "api", f"repos/{SOURCE}/actions/{path}"], check=True, capture_output=True).stdout
    def get(path):
        return json.loads(api(path))
    recovery = get(f"runs/{recovery_id}")
    jobs = get(f"runs/{recovery_id}/jobs?per_page=100")
    metadata = get(f"artifacts/{artifact_id}")
    raw = api(f"artifacts/{artifact_id}/zip")
    require(len(raw) <= 1_000_000, "Receipt archive too large")
    validate_archive(metadata, raw, artifact_id, digest, "validated-draft-1301", recovery_id, recovery["head_sha"])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1 and entries[0].filename == "validated-draft-receipt.json" and
                entries[0].file_size <= 500_000 and not entries[0].is_dir() and
                (entries[0].external_attr >> 16) & 0o170000 != 0o120000, "Unexpected receipt archive")
        receipt = json.loads(archive.read(entries[0]).decode("utf-8-sig"))
    draft = validate_receipt(receipt, recovery, jobs, source_run, source_jobs, recovery_id, release_id)
    metadata = get(f"artifacts/{BANKED_ARTIFACT}")
    raw = api(f"artifacts/{BANKED_ARTIFACT}/zip")
    validate_archive(metadata, raw, BANKED_ARTIFACT, BANKED_DIGEST, "verified-channel", BANKED_RUN, BANKED_SOURCE)
    incoming = Path(incoming)
    require(not incoming.exists(), "Preserve incoming evidence")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 2 and {e.filename for e in entries} == {"channel.zip", "release-evidence.json"} and
                sum(e.file_size for e in entries) <= 1_000_000_000 and
                all(not e.is_dir() and (e.external_attr >> 16) & 0o170000 != 0o120000 for e in entries),
                "Unexpected banked channel artifact")
        incoming.mkdir()
        archive.extractall(incoming)
    evidence = json.loads((incoming / "release-evidence.json").read_text(encoding="utf-8-sig"))
    require(receipt["signedFiles"] == evidence["files"] and evidence["toolingSource"] == BANKED_SOURCE and
            evidence["identity"] == dict(code=1301, version="0.13.1", protocol=17, readModel=14, command=14,
                                         authoritySchema=5, clientSchema=2, packageId="app.thekeep.keep"),
            "Signed inventory/compatibility mismatch")
    for name in ("channel.zip", "release-evidence.json"):
        data = (incoming / name).read_bytes()
        _, size, hash_value = BANKED_ASSETS[name]
        require(len(data) == size and hashlib.sha256(data).hexdigest() == hash_value, "Banked asset bytes changed")
    # Authenticated receipt attests to both RSA verifications; exact original archive is independently pinned.
    validate_channel(source_run, incoming, draft, out)
    # Refuse a producer/recovery rerun that occurred during retrieval, before Pages upload.
    current_recovery = get(f"runs/{recovery_id}")
    require(all(current_recovery[k] == recovery[k] for k in
                ("id", "head_sha", "run_attempt", "status", "event", "head_branch", "path", "actor", "triggering_actor")),
            "Recovery changed during handoff")
    current = get(f"runs/{BANKED_RUN}")
    require(current["head_sha"] == BANKED_SOURCE and current["run_attempt"] == 1, "Original producer rerun")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("run", "jobs", "incoming", "draft", "out"):
        parser.add_argument(f"--{name}", required=name in ("run", "jobs"))
    for name in ("recovery-run", "receipt-artifact-id", "draft-release-id"):
        parser.add_argument(f"--{name}", type=int)
    parser.add_argument("--receipt-digest")
    args = parser.parse_args()
    run = json.loads(Path(args.run).read_text())
    validate_run(run, json.loads(Path(args.jobs).read_text()))
    if args.recovery_run:
        require(args.receipt_artifact_id and args.receipt_digest and args.draft_release_id and args.incoming and args.out,
                "Incomplete immutable receipt inputs")
        consume_handoff(run, json.loads(Path(args.jobs).read_text()), args.recovery_run, args.receipt_artifact_id,
                        args.receipt_digest, args.draft_release_id, args.incoming, args.out)
    elif args.incoming:
        validate_channel(run, args.incoming, json.loads(Path(args.draft).read_text()), args.out)
    print("Verified owner source preparation" + (" and static channel bytes" if args.incoming else ""))
