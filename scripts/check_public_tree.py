"""Offline, best-effort publication check. Never prints matching secret values."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess


RULES = [
    ("private-home-path", re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+(?:/|\b)|[A-Za-z]:\\Users\\[^\\\s]+")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("credential-url", re.compile(r"https?://[^\s/:@]+:[^\s/@]+@")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}")),
]
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PRIVATE_DIRECTORIES = {".venv", ".codex", ".private", "local", "build", "dist", "logs", "recordings", "transcripts", "screenshots"}
PRIVATE_SUFFIXES = {".log", ".pem", ".key", ".p12", ".pfx", ".wav", ".mp3", ".m4a", ".mp4", ".sqlite", ".db"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    revision: str = "working-tree"


def private_email(value: str) -> bool:
    domain = value.rsplit("@", 1)[-1].lower()
    return not (domain in {"example.com", "example.org", "example.net", "noreply.github.com"}
                or domain.endswith((".noreply.github.com", ".example", ".invalid", ".test")))


def private_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    if any(part in PRIVATE_DIRECTORIES or part.endswith(".app") for part in parts):
        return True
    if parts and parts[0] == "knowledge" and path != "knowledge/sample_notes.txt":
        return True
    return ((name == ".env" or name.startswith(".env.")) and name != ".env.example"
            or name.endswith(".local.toml") or PurePosixPath(path).suffix.lower() in PRIVATE_SUFFIXES)


def scan_content(path: str, data: bytes, revision="working-tree") -> list[Finding]:
    findings = [Finding(path, 0, "private-file", revision)] if private_path(path) else []
    # Binary assets still need human inspection; do not decode them as documents.
    if b"\0" in data:
        return findings
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        for name, pattern in RULES:
            if pattern.search(line):
                findings.append(Finding(path, number, name, revision))
        if any(private_email(match.group()) for match in EMAIL.finditer(line)):
            findings.append(Finding(path, number, "email-review", revision))
    return findings


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def scan_worktree(root: Path) -> tuple[list[Finding], int]:
    candidates = set(git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")) - {b""}
    findings = []
    count = 0
    for raw in sorted(candidates):
        name = raw.decode("utf-8", errors="surrogateescape")
        path = root / name
        if path.is_symlink():
            findings.append(Finding(name, 0, "symlink-review"))
            continue
        if not path.is_file():
            continue
        count += 1
        findings.extend(scan_content(name, path.read_bytes()))
    return findings, count


def scan_history(root: Path) -> tuple[list[Finding], int, int]:
    commits = git(root, "rev-list", "--all").decode().splitlines()
    seen = set()
    findings = []
    for commit in commits:
        message = git(root, "show", "-s", "--format=%B", commit)
        findings.extend(scan_content("<commit-message>", message, commit[:12]))
        for entry in git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not entry:
                continue
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, blob = metadata.split()
            name = raw_name.decode("utf-8", errors="surrogateescape")
            if kind != b"blob" or (name, blob) in seen:
                continue
            seen.add((name, blob))
            if mode == b"120000":
                findings.append(Finding(name, 0, "symlink-review", commit[:12]))
            else:
                findings.extend(scan_content(name, git(root, "cat-file", "blob", blob.decode()), commit[:12]))
    emails = set(git(root, "log", "--all", "--format=%ae%n%ce").decode().splitlines())
    return findings, len(commits), sum(private_email(email) for email in emails if email)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Also scan all locally reachable Git commits")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        findings, files = scan_worktree(root)
        print(f"Checked {files} publication-candidate files (tracked + non-ignored untracked).")
        if args.history:
            historical, commits, email_count = scan_history(root)
            findings.extend(historical)
            print(f"Checked file contents and messages in {commits} reachable commits.")
            if email_count:
                print(f"REVIEW: Git author/committer metadata contains {email_count} non-anonymous email address(es).")
                print("History was not modified. Review metadata before publication.")
    except (OSError, subprocess.CalledProcessError):
        print("Could not complete the check; run inside a readable Git checkout.")
        return 2
    for finding in findings:
        print(f"{finding.revision} {finding.path}:{finding.line}: {finding.rule}")
    print("No matching secret values are printed. This check does not prove the absence of sensitive data.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
