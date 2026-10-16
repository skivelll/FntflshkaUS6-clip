"""Fixed, developer-selected storage budget. No user-adjustable quotas."""
LIBRARY_MAX = 820 * 1024
BLOCK_SIZE = 4096
SERVICE_RESERVE = 160 * 1024
# Charge at least one erase block per file, including LittleFS inline files.
# This is conservative quota accounting, not a measurement of allocated blocks.
MAX_SCENES = LIBRARY_MAX // BLOCK_SIZE
MAX_ID = 65535
MAX_FRAMES = 65535  # uint16 in the existing animation prefix; file budget dominates.


def allocated_size(size):
    return ((size + BLOCK_SIZE - 1) // BLOCK_SIZE) * BLOCK_SIZE


def library_fits(used, old_size, new_size):
    result = used - allocated_size(old_size) + allocated_size(new_size)
    # Preserve pre-upgrade collections; allow them to shrink without deleting data.
    return result <= LIBRARY_MAX or (used > LIBRARY_MAX and result < used)
