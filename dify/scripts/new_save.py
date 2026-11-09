"""Fresh random capability codes provide a nondestructive reset."""

import secrets

print("save-" + secrets.token_hex(12))
