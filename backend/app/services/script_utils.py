"""
Shared utilities for user-script processing.
"""
import re
from pathlib import Path

# config_files/ sits at backend/config_files/
CONFIG_ROOT = Path(__file__).parent.parent.parent / "config_files"

# Matches ##!! or ##! ! (Zero Touch sometimes inserts a space between the two !)
_FILE_MARKER_RE = re.compile(r"^##!\s*!\s*(.+)")


def apply_script_substitutions(user_script: str, gateway_name: str) -> str:
    """
    Apply ##!! file injection and <gateway-name> substitution to a user-script string.

    ##!! <filename> lines (or ##! ! after Zero Touch mangles them) are replaced
    with the content of the referenced file, looked up first in
    config_files/<gateway-name>/, then in config_files/.
    """
    if not user_script:
        return user_script

    if "<gateway-name>" in user_script and gateway_name:
        user_script = user_script.replace("<gateway-name>", gateway_name)

    config_gw_dir = CONFIG_ROOT / gateway_name
    processed_lines = []
    for line in user_script.splitlines():
        m = _FILE_MARKER_RE.match(line.strip())
        if m:
            file_ref = m.group(1).strip()
            config_file = config_gw_dir / file_ref
            if not config_file.exists():
                config_file = CONFIG_ROOT / file_ref
            if config_file.exists():
                file_content = config_file.read_text(encoding="utf-8")
                processed_lines.append(f"#! content of {file_ref}")
                processed_lines.append(file_content.rstrip())
            else:
                processed_lines.append(
                    f"#! file not found: {file_ref} (checked {config_gw_dir} and {CONFIG_ROOT})"
                )
        else:
            processed_lines.append(line)
    return "\n".join(processed_lines)
