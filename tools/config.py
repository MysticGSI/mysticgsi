"""
Partition lists and constants shared across tools.
"""


BLOCK_SIZE = 4096

# Partitions extracted from firmware and unpacked for porting.
DEFAULT_PARTITIONS: list[str] = [
    'system',
    'system_ext',
    'product',
    'my_bigball',
    'my_carrier',
    'my_product',
    'my_stock',
    'my_engineering',
    'my_manifest',
    'my_heytap',
    'my_region',
    'hw_product',
    'vendor',
    'preas',
    'preavs',
    'product_h',
    'odm',
    'mi_ext',
    'tr_carrier',
    'tr_company',
    'tr_mi',
    'tr_preload',
    'tr_product',
    'tr_region',
    'tr_theme',
    'prism',
    'optics',
]
