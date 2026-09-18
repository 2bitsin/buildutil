"""`buildutil setup-skill` — install the buildutil skill for a coding
agent (stdlib-only, pre-venv, like init).

The skill (templates/skill/) teaches an agent the CLI and the
directory conventions. It installs into the agent's project-level
config dir — `.claude/skills/buildutil/` and friends — chosen by
--agent-type or autodetected from which dot directories exist at the
project root. `--zip` writes the same skill as a zip for manual
installation elsewhere. `init` and `setup` install it automatically
for every detected agent unless --no-agents.
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent / "templates" / "skill"

# agent name -> (detection dot-dir, skills dir the skill installs under).
# Only the claude path is a vendor-documented location; the others follow
# the same shape under their own config dir.
AGENTS = {
  "claude":  (".claude", ".claude/skills"),
  "codex":   (".codex", ".codex/skills"),
  "qwen":    (".qwen", ".qwen/skills"),
  "gemini":  (".gemini", ".gemini/skills"),
  "copilot": (".github", ".github/skills"),
  "cursor":  (".cursor", ".cursor/skills"),
}


def detect(root: Path) -> list[str]:
  """Agents present at the project root, by their dot dir."""
  return [name for name, (dot, _) in AGENTS.items()
          if (root / dot).is_dir()]


def install_for(root: Path, agent: str) -> Path:
  """Copy the skill to the agent's skills dir; returns the destination.
  The copy is OURS — regenerated on every install/setup/init — so it is
  simply rewritten, never merged."""
  _, skills = AGENTS[agent]
  dst = root / skills / "buildutil"
  if dst.exists():
    shutil.rmtree(dst)
  shutil.copytree(SKILL_DIR, dst)
  print(f"  buildutil skill installed for {agent}: "
        f"{dst.relative_to(root)}/")
  return dst


def write_zip(out: Path) -> Path:
  """The same skill as a zip (skill-transport convention): buildutil/
  SKILL.md and siblings, for installing where this CLI isn't."""
  out = out if out.suffix == ".zip" else out.with_suffix(".zip")
  with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(SKILL_DIR.rglob("*")):
      if p.is_file():
        z.write(p, Path("buildutil") / p.relative_to(SKILL_DIR))
  print(f"skill zipped at {out}")
  return out


def auto_install(root: Path) -> list[str]:
  """The init/setup hook: install for every agent whose dot dir exists.
  Quiet no-op when none — a project without agents owes nobody a
  skills tree."""
  agents = detect(root)
  for agent in agents:
    install_for(root, agent)
  return agents


def main(argv: list[str]) -> None:
  ap = argparse.ArgumentParser(
    prog="buildutil setup-skill",
    description="install the buildutil skill for a coding agent "
                "(autodetected from the project's dot dirs unless "
                "--agent-type says which)")
  ap.add_argument("--agent-type", choices=sorted(AGENTS),
                  help="install for this agent (default: every agent "
                       "detected at the project root)")
  ap.add_argument("--zip", metavar="PATH", default=None,
                  help="also write the skill as a zip at PATH")
  a = ap.parse_args(argv)

  from .updatecmd import project_root
  root = project_root() or Path.cwd()
  if a.zip:
    write_zip(Path(a.zip))
  if a.agent_type:
    install_for(root, a.agent_type)
    return
  agents = auto_install(root)
  if not agents and not a.zip:
    raise SystemExit(
      "buildutil setup-skill: no agent detected at "
      f"{root} (looked for: "
      + ", ".join(dot for dot, _ in AGENTS.values())
      + "). Pass --agent-type "
      + "|".join(sorted(AGENTS)) + ", or --zip PATH for a portable copy.")
