"""Build a source-only AcFun publisher ZIP, without runtime files or secrets."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SKILL = Path(__file__).resolve().parents[1] / "skills" / "acfun-publisher"
FILES = (
    "SKILL.md", "README.md", "requirements.txt",
    "scripts/acfun_auth.py", "scripts/acfun_client.py", "scripts/acfun_article.py",
    "scripts/acfun_video.py", "scripts/inbox.py", "references/assets-schema.md",
)


def package(source, output):
    source = source.resolve()
    for name in FILES:
        path = source / name
        if not path.is_file() or path.resolve() != path:
            raise ValueError(f"分发源文件缺失或为符号链接：{name}")
    # Use an explicit manifest so newly created credentials can never enter the ZIP.
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(source / name, f"acfun-publisher/{name}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("acfun-publisher.zip"))
    args = parser.parse_args()
    try:
        package(SKILL, args.out)
    except (ValueError, OSError) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"已生成纯源码分发包：{args.out}")


if __name__ == "__main__":
    main()
