#!/usr/bin/env python3
"""profile_route.py: a job title -> its profile base -> the resume PDF a mass application sends.

A mass application sends the resume that belongs to the title's profile, not the general base. The profile bases live
in resumes/bases/profiles/<slug>/. This resolver is the one place that answers "which resume does this title send?":

  title --focus_profiles.pick--> lens --LENS_TO_PROFILE--> profile slug --usable?--> that profile's PDF
                                                                          \\--no----> the general base PDF

A profile is USABLE only when all three hold (nothing else is read, nothing is guessed):
  1. resumes/bases/profiles/<slug>/resume.yaml exists;
  2. resumes/bases/profiles/<slug>/signed/<sha12>.yaml exists, where sha12 = sha256(resume.yaml)[:12] (the editor
     signed exactly these bytes; an edit after the signature changes the hash and the profile stops being usable);
  3. resumes/bases/profiles/<slug>/<identity.resume_pdf_name> exists (for --pdf inline: the inline-dates PDF under
     inline/).
Anything else (no lens match, a lens with no profile, a profile not usable) falls back to the general base
(resumes/bases/general), whose PDF and resume.yaml are verified against resumes/bases/general/SHA256.txt (the hash is
read from that file, never written here). A general base that does not match its pins raises BaseNotPinned: nothing
ships on an unverified page.

Only a confident title match routes (focus_profiles score >= MIN_SCORE: a specialist word, a titles_covered pattern
or a pattern the title contains). A word-overlap score below that is not a match for a mass send.

Usage:
  python3 scripts/profile_route.py "<title>" [--pdf classic|inline] [--profile auto|general|<slug>] [--json]
  python3 scripts/profile_route.py --status          # every profile: usable or why not
Exit 0 resolved, 1 BaseNotPinned, 2 usage error. No network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import focus_profiles  # noqa: E402

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
PROFILES_DIR = Path("resumes") / "bases" / "profiles"
GENERAL_DIR = Path("resumes") / "bases" / "general"
_PDF = str(cfg.get("identity.resume_pdf_name", "Resume.pdf"))
PDF_NAMES = {"classic": _PDF, "inline": f"{Path(_PDF).stem}_inline-dates.pdf"}
MIN_SCORE = 1.5          # focus_profiles.pick: 1.5 = the title contains a covered pattern, 2.0 = matches one, 2.25 = a
                         # specialist word; below that is word overlap (0.34-1.0), too loose to choose a resume

# lens family (profile/lenses/<family>.md) -> profile slug (resumes/bases/profiles/<slug>/), from
# policy.profile_route.lens_to_profile. Several lenses may route to one profile. A lens with no row has no profile
# package: its titles still resolve to a lens, the route says "no profile for lens X" and the general base is sent.
LENS_TO_PROFILE: dict[str, str] = {str(k): str(v) for k, v in
                                   (cfg.get("policy.profile_route.lens_to_profile", {}) or {}).items()}
PROFILE_SLUGS = tuple(dict.fromkeys(LENS_TO_PROFILE.values()))


class BaseNotPinned(RuntimeError):
    """The general base PDF or resume.yaml is missing or does not match resumes/bases/general/SHA256.txt."""


@dataclass(frozen=True)
class Route:
    title: str
    lens: str | None          # the lens focus_profiles picked first (None: no confident match)
    profile: str | None       # the profile slug whose PDF is sent (None: the general base)
    source: str               # "profile" | "general"
    pdf: str                  # the PDF to send, relative to the job-search root
    resume_yaml: str          # the resume.yaml that PDF was built from, relative to the root
    why: str                  # one line: the match, and for a fallback the reason

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile_status(root: Path, slug: str, pdf: str = "classic") -> tuple[bool, str]:
    """(usable, why not). The three conditions of the module docstring."""
    folder = Path(root) / PROFILES_DIR / slug
    resume = folder / "resume.yaml"
    if not resume.is_file():
        return False, f"{PROFILES_DIR.as_posix()}/{slug}/resume.yaml is missing"
    sha12 = sha256_file(resume)[:12]
    if not (folder / "signed" / f"{sha12}.yaml").is_file():
        return False, (f"{PROFILES_DIR.as_posix()}/{slug}/resume.yaml (sha {sha12}) has no signed/{sha12}.yaml: "
                       "unsigned, or edited after it was signed")
    pdf_path = _profile_pdf(folder, pdf)
    if not pdf_path.is_file():
        return False, f"{pdf_path.relative_to(Path(root)).as_posix()} is missing"
    return True, ""


def _profile_pdf(folder: Path, pdf: str) -> Path:
    return folder / PDF_NAMES[pdf] if pdf == "classic" else folder / "inline" / PDF_NAMES[pdf]


def _pins(root: Path) -> dict[str, str]:
    path = Path(root) / GENERAL_DIR / "SHA256.txt"
    pins: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                pins[parts[1]] = parts[0]
    return pins


def general_base(root: Path, pdf: str = "classic") -> tuple[str, str]:
    """(PDF, resume.yaml) of the general base, relative to the root, each verified against
    resumes/bases/general/SHA256.txt. Raises BaseNotPinned on a missing file, a missing pin or a different hash."""
    pins = _pins(root)
    out = []
    for name in (PDF_NAMES[pdf], "resume.yaml"):
        path = Path(root) / GENERAL_DIR / name
        if not path.is_file():
            raise BaseNotPinned(f"{GENERAL_DIR.as_posix()}/{name} is missing")
        if name not in pins:
            raise BaseNotPinned(f"{GENERAL_DIR.as_posix()}/SHA256.txt has no pin for {name}")
        if sha256_file(path) != pins[name]:
            raise BaseNotPinned(f"{GENERAL_DIR.as_posix()}/{name} does not match {GENERAL_DIR.as_posix()}/SHA256.txt")
        out.append((GENERAL_DIR / name).as_posix())
    return out[0], out[1]


def lens_for(root: Path, title: str) -> tuple[str | None, str]:
    """(the first lens focus_profiles picks with score >= MIN_SCORE, why) or (None, why not)."""
    picked = focus_profiles.pick(Path(root), title)
    if not picked:
        return None, "no lens matches the title"
    top = picked[0]
    if top["score"] < MIN_SCORE:
        return None, f"best lens {top['family']} scores {top['score']} (< {MIN_SCORE}): too loose to choose a resume"
    return top["family"], f"lens {top['family']} {top['score']:.2f} ({top['why']})"


def resolve(root: Path, title: str, pdf: str = "classic", profile: str = "auto",
            verify_general: bool = True) -> Route:
    """The route for a title. `profile`: "auto" (by title), "general" (force the general base) or a profile slug (force
    it; still falls back when it is not usable). verify_general=False skips the SHA256.txt check on the fallback
    (a caller that pins the base itself, as apply_batch does)."""
    if pdf not in PDF_NAMES:
        raise ValueError(f"pdf must be one of {sorted(PDF_NAMES)}")
    root = Path(root)
    lens, why = lens_for(root, title)
    slug: str | None = None
    if profile == "general":
        why = "general base forced"
    elif profile != "auto":
        if profile not in PROFILE_SLUGS:
            raise ValueError(f"unknown profile '{profile}' (one of {', '.join(PROFILE_SLUGS)})")
        slug, why = profile, f"profile {profile} forced"
    elif lens is not None:
        slug = LENS_TO_PROFILE.get(lens)
        if slug is None:
            why += f"; no profile package for lens {lens}"
    if slug is not None:
        ok, bad = profile_status(root, slug, pdf)
        if ok:
            folder = PROFILES_DIR / slug
            return Route(title, lens, slug, "profile", _profile_pdf(folder, pdf).as_posix(),
                         (folder / "resume.yaml").as_posix(), f"{why}; profile {slug} signed")
        why += f"; profile {slug} not usable: {bad}"
    if verify_general:
        general_pdf, general_yaml = general_base(root, pdf)
    else:
        general_pdf, general_yaml = (GENERAL_DIR / PDF_NAMES[pdf]).as_posix(), (GENERAL_DIR / "resume.yaml").as_posix()
    return Route(title, lens, None, "general", general_pdf, general_yaml, f"{why}; general base")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("title", nargs="?")
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--pdf", choices=sorted(PDF_NAMES), default="classic")
    p.add_argument("--profile", default="auto", help="auto (by title), general, or a profile slug")
    p.add_argument("--json", action="store_true")
    p.add_argument("--status", action="store_true", help="list every profile: usable, or why not")
    a = p.parse_args(argv)
    if a.status:
        for slug in PROFILE_SLUGS:
            ok, bad = profile_status(a.root, slug, a.pdf)
            print(f"{slug:20} {'usable' if ok else 'NOT usable: ' + bad}")
        return 0
    if not a.title:
        p.print_usage(sys.stderr)
        return 2
    try:
        route = resolve(a.root, a.title, a.pdf, a.profile)
    except BaseNotPinned as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(route.as_dict(), indent=2))
    else:
        print(f"{route.title!r}: {route.profile or 'general base'} ({route.source}) -> {route.pdf}\n  {route.why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
