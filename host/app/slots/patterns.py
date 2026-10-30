"""
Паттерны для наблюдения за загрузкой платы через COM1.
Все паттерны хранятся в нижнем регистре для быстрого сравнения.
"""

BIOS_PATTERNS = [
    "pcie: type 1 ",
    "bios ",
]

REBOOT_PATTERNS = [
    "reboot: restarting system ",
    "systemd-reboot.service ",
    "reached target reboot.target ",
    "the system is going down for reboot ",
]

SHUTDOWN_PATTERNS = [
    "power down ",
    "systemd-shutdown ",
    "the system is going down for halt ",
    "the system is going down for poweroff ",
]

POWER_ON_PATTERNS = [
    "created slice system-modprobe.slice ",
    "plymouth-start.service ",
    "reached target basic.target ",
    "reached target sysinit.target ",
    "dracut-initqueue.service ",
]

LOGIN_PROMPT_PATTERNS = [
    "login: ",
]