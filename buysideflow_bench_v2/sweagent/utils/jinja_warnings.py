import re

from sweagent.utils.log import get_logger


# Warn on likely mistaken single-brace placeholders such as `{var}` but ignore
# literal JSON/examples like `{"mode": "sql"}`.
_SUSPICIOUS_SINGLE_BRACE_VAR = re.compile(r"(?<!\{)\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}(?!\})")


def _warn_probably_wrong_jinja_syntax(template: str | None) -> None:
    """Warn if the template uses {var} instead of {{var}}."""
    if template is None:
        return
    if "{" not in template:
        return
    if _SUSPICIOUS_SINGLE_BRACE_VAR.search(template) is None:
        return
    logger = get_logger("swea-config", emoji="🔧")
    logger.warning("Probably wrong Jinja syntax in template: %s. Make sure to use {{var}} instead of {var}.", template)
