import re

_ANSI_CSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
_ANSI_OSC_RE = re.compile(r'\x1b\][^\x1b]*(?:\x1b\\|\x07)')


def strip_ansi(text: str) -> str:
    text = _ANSI_CSI_RE.sub('', text)
    text = _ANSI_OSC_RE.sub('', text)
    return text
