from app.config import AppConfig, SlotConfig, WsConfig
from app.channels.serial_channel import SerialChannel
from app.channels.ws_channel import WsChannel
from slots.slot_fabric import SlotFabric


def make_config(n_slots: int = 2) -> AppConfig:
    slots = {
        f"slot{i}": SlotConfig(
            slot_id=f"slot{i}",
            com_log=f"/dev/log{i}",
            com_ctrl=f"/dev/ctrl{i}",
        )
        for i in range(1, n_slots + 1)
    }
    return AppConfig(ws=WsConfig(host="127.0.0.1", port=0), slots=slots)


def test_registry_builds_all_slots():
    registry = SlotFabric(make_config(4))
    assert set(registry.all_slot_ids()) == {"slot1", "slot2", "slot3", "slot4"}
    for slot_id in registry.all_slot_ids():
        slot = registry.get(slot_id)
        assert slot is not None
        assert isinstance(slot.ws_channel, WsChannel)
        assert isinstance(slot.serial_channel, SerialChannel)
        assert slot.log_watcher.slot_id == slot_id


def test_router_registers_all_ws_channels():
    registry = SlotFabric(make_config(3))
    assert set(registry.router._channels) == {"slot1", "slot2", "slot3"}


def test_get_unknown_returns_none():
    registry = SlotFabric(make_config(1))
    assert registry.get("slotX") is None
