import subprocess
from datetime import datetime


def get_git_diff():
    result = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def generate_summary(diff):
    lines = diff.split("\n")
    changed_files = set()

    for line in lines:
        if line.startswith("+++ b/"):
            changed_files.add(line.replace("+++ b/", ""))

    summary = f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    summary += "### 변경 파일\n"
    for file_path in sorted(changed_files):
        summary += f"- {file_path}\n"

    return summary


def update_changelog(summary):
    with open("CHANGELOG.md", "a", encoding="utf-8") as changelog:
        changelog.write(summary + "\n")


def main():
    diff = get_git_diff()
    if diff.strip():
        summary = generate_summary(diff)
        update_changelog(summary)


if __name__ == "__main__":
    main()
