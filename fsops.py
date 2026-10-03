import glob as _glob
import os
import re
import shutil
import subprocess

from tools.host import posix

CHUNK = 1024 * 1024
BLOCK = 4096


def run(argv, *, cwd=None, stdin=None):
    argv = [str(a) for a in argv]
    fh = open(stdin, "rb") if stdin else None
    try:
        return subprocess.run(argv, cwd=cwd, stdin=fh).returncode
    except (FileNotFoundError, NotADirectoryError):
        return 127
    finally:
        if fh:
            fh.close()


def _allocated(st: os.stat_result):
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return -(-st.st_size // BLOCK) * BLOCK
    return blocks * 512


def disk_usage(path):
    """Allocated bytes under path, like `du -s`."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    total = _allocated(st)
    if not os.path.isdir(path) or islink(path):
        return total

    seen = set()
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            # Hard links share an inode; count it once, like du does.
            if st.st_nlink > 1 and not os.path.isdir(os.path.join(root, name)):
                if (st.st_dev, st.st_ino) in seen:
                    continue
                seen.add((st.st_dev, st.st_ino))
            total += _allocated(st)
    return total


def drop_lines(path, pattern):
    try:
        with open(path, "r", errors="surrogateescape") as f:
            lines = f.readlines()
    except OSError:
        return 0
    rx = re.compile(pattern)
    kept = [line for line in lines if not rx.search(line)]
    removed = len(lines) - len(kept)
    if removed:
        with open(path, "w", errors="surrogateescape") as f:
            f.writelines(kept)
    return removed


def sub_lines(path, pattern, repl):
    try:
        with open(path, "r", errors="surrogateescape") as f:
            text = f.read()
    except OSError:
        return 0
    new, n = re.subn(pattern, repl.replace("\\", "\\\\"), text)
    if n:
        with open(path, "w", errors="surrogateescape") as f:
            f.write(new)
    return n


def set_props(path, key, value):
    try:
        with open(path, "r", errors="surrogateescape") as f:
            lines = f.readlines()
    except OSError:
        return False
    hit = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}\n"
            hit = True
    if hit:
        with open(path, "w", errors="surrogateescape") as f:
            f.writelines(lines)
    return hit


def append_file(src, dst):
    data = b""
    for p in expand(src):
        try:
            with open(p, "rb") as f:
                data += f.read()
        except OSError:
            pass
    if not data:
        return False
    mkdirp(os.path.dirname(dst))
    with open(dst, "ab") as f:
        f.write(data)
    return True


def append_text(path, text):
    mkdirp(os.path.dirname(path))
    with open(path, "a", errors="surrogateescape") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def expand(pattern):
    if any(ch in str(pattern) for ch in "*?["):
        return sorted(_glob.glob(str(pattern)))
    return [str(pattern)]


def rmrf(path):
    for p in expand(path):
        try:
            if os.path.isdir(p) and not islink(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.lexists(p):
                os.remove(p)
        except OSError:
            pass


def mkdirp(path):
    if path:
        os.makedirs(path, exist_ok=True)


def touch(path):
    mkdirp(os.path.dirname(path))
    open(path, "a").close()


def symlink(target, link):
    mkdirp(os.path.dirname(link))
    if not os.path.lexists(link):
        posix.symlink(os.fsdecode(target), os.path.abspath(os.fsdecode(link)))


def readlink(path):
    if os.path.islink(path):
        return os.readlink(path)
    if os.name == "nt" and os.path.isfile(path):
        return posix.readlink(os.fsdecode(path))
    return ""


def islink(path):
    return bool(readlink(path))


def _target(src, dst):
    if os.path.isdir(dst):
        return os.path.join(dst, os.path.basename(src))
    return dst


def copy_file(src, dst):
    if not os.path.lexists(src):
        return False
    mkdirp(os.path.dirname(dst) if not os.path.isdir(dst) else dst)
    target = _target(src, dst)
    if os.path.lexists(target):
        rmrf(target)
    shutil.copy2(src, target, follow_symlinks=False)
    return True


def cp_r(src, dst, *, clobber=True, exclude=None):
    for s in expand(src):
        if exclude and exclude(os.path.basename(s)):
            continue
        if not os.path.lexists(s):
            continue
        target = _target(s, dst)
        if not clobber and os.path.lexists(target):
            if (
                os.path.isdir(s)
                and not islink(s)
                and os.path.isdir(target)
                and not islink(target)
            ):
                for name in os.listdir(s):
                    cp_r(
                        os.path.join(s, name), target,
                        clobber=False, exclude=exclude,
                    )
            continue
        if islink(s):
            if os.path.lexists(target):
                rmrf(target)
            mkdirp(os.path.dirname(target))
            symlink(readlink(s), target)
        elif os.path.isdir(s):
            if os.path.lexists(target) and (
                islink(target) or not os.path.isdir(target)
            ):
                rmrf(target)
            mkdirp(target)
            for name in os.listdir(s):
                cp_r(
                    os.path.join(s, name), target,
                    clobber=clobber, exclude=exclude,
                )
            shutil.copystat(s, target)
        else:
            mkdirp(os.path.dirname(target))
            if os.path.lexists(target):
                rmrf(target)
            shutil.copy2(s, target, follow_symlinks=False)


def copy_into(src_dir, dst_dir, *, clobber=True):
    mkdirp(dst_dir)
    cp_r(os.path.join(str(src_dir).rstrip("/"), "*"), dst_dir, clobber=clobber)


def move(src, dst):
    for s in expand(src):
        if not os.path.lexists(s):
            continue
        target = _target(s, dst)
        if os.path.lexists(target):
            rmrf(target)
        mkdirp(os.path.dirname(target))
        shutil.move(s, target)
