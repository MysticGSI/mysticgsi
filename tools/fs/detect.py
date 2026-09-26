"""
In-memory binary magic detector for filesystem images.
"""


def detect_filesystem(file_path: str) -> str:
    """
    Detects whether an image file contains an ext4, erofs, or f2fs filesystem.
    Returns: 'ext4', 'erofs', 'f2fs', or 'unknown'
    """
    formats = ([b'\xe2\xe1\xf5\xe0', "erofs", 1024], [b'\x53\xef', 'ext4', 1080], [b'\x10\x20\xF5\xF2', 'f2fs', 1024])
    try:
        with open(file_path, 'rb') as f:
            for header, desc, offset in formats:
                f.seek(offset)
                if f.read(len(header)) == header:
                    return desc
    except OSError:
        pass

    return 'unknown'
