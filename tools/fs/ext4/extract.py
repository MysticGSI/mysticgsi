"""
Extracts the tree of an ext2/3/4 image into a host directory.
"""

import os

from .filesystem import ROOT_INODE, Ext4Error, Ext4Filesystem

_OPEN_FLAGS = (os.O_WRONLY | os.O_CREAT | os.O_TRUNC
               | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))


def _make_dir(path):
    try:
        os.mkdir(path)
    except FileExistsError:
        # On case-insensitive hosts two names can collide; never descend
        # through a symlink that an earlier entry created.
        if os.path.islink(path) or not os.path.isdir(path):
            raise


def _write_file(fs, inode, path):
    fd = os.open(path, _OPEN_FLAGS, 0o666)
    with os.fdopen(fd, 'wb') as out:
        fs.write_file(inode, out)
    try:
        os.utime(path, ns=(inode.atime_ns, inode.mtime_ns))
    except (OSError, OverflowError, ValueError):
        pass


def _write_symlink(fs, inode, path):
    target = os.fsdecode(fs.read_link(inode))
    try:
        os.symlink(target, path)
    except OSError:
        with open(f"{path}.symlink", 'w', encoding='utf-8',
                  errors='surrogateescape') as f:
            f.write(target)


def extract_ext4(image_path: str, output_dir: str, logger=None) -> bool:
    """
    Extracts every directory, regular file and symlink of a raw ext2/3/4
    image into output_dir. Returns True only if nothing failed.
    """
    if not os.path.isfile(image_path):
        return False
    log = logger or (lambda message: None)

    try:
        fs = Ext4Filesystem(image_path)
    except Exception as e:
        log(f"Failed to extract ext4 image {image_path}: {e}")
        return False

    with fs:
        try:
            root = fs.read_inode(ROOT_INODE)
            if not root.is_dir:
                raise Ext4Error("root inode is not a directory")
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            log(f"Failed to extract ext4 image {image_path}: {e}")
            return False

        failures = 0
        visited = {ROOT_INODE}
        pending = [(root, output_dir, "")]
        while pending:
            directory, dest, rel = pending.pop()
            try:
                entries = list(fs.iter_dir(directory))
            except Exception as e:
                log(f"Error extracting {rel or '/'} "
                    f"(inode {directory.number}): {e}")
                failures += 1
                continue
            for name, number in entries:
                name = os.fsdecode(name)
                child_rel = f"{rel}/{name}" if rel else name
                try:
                    if "/" in name or "\0" in name:
                        raise Ext4Error("invalid file name")
                    inode = fs.read_inode(number)
                    path = os.path.join(dest, name)
                    if inode.is_dir:
                        if number in visited:
                            raise Ext4Error("directory loop")
                        visited.add(number)
                        _make_dir(path)
                        pending.append((inode, path, child_rel))
                    elif inode.is_file:
                        _write_file(fs, inode, path)
                    elif inode.is_symlink:
                        _write_symlink(fs, inode, path)
                except Exception as e:
                    log(f"Error extracting {child_rel} (inode {number}): {e}")
                    failures += 1

    if failures:
        log(f"{failures} entries of {image_path} could not be extracted")
        return False
    return True
