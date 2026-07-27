import importlib
import json
import sys
import types

import pytest


class FakeObject:
    def get_editor_property(self, name):
        return getattr(self, name)

    def set_editor_property(self, name, value):
        setattr(self, name, value)


class FakeKey:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeKey) and self.name == other.name


class FakeMapping(FakeObject):
    def __init__(self, action, key):
        self.action = action
        self.key = key


class FakeMappingData(FakeObject):
    def __init__(self, context):
        self.context = context

    @property
    def mappings(self):
        return self.context.current_mappings


class FakeContext(FakeObject):
    def __init__(self, action, f9_mappings):
        self.current_mappings = [
            FakeMapping(action, FakeKey("F9")) for _ in range(f9_mappings)
        ]
        self.default_key_mappings = FakeMappingData(self)
        self.unmapped = []
        self.mapped = []

    def unmap_key(self, action, key):
        self.unmapped.append((action, str(key)))
        for index, mapping in enumerate(self.current_mappings):
            if mapping.action is action and str(mapping.key) == str(key):
                self.current_mappings.pop(index)
                break

    def map_key(self, action, key):
        self.mapped.append((action, str(key)))
        self.current_mappings.append(FakeMapping(action, key))


class FakeUnreal:
    def __init__(self, action_exists, f9_mappings=1):
        self.action_exists = action_exists
        self.action = FakeObject()
        self.action.value_type = "Axis1D"
        self.context = FakeContext(self.action, f9_mappings)
        self.default_object = FakeObject()
        self.default_object.capture_action = self.action if action_exists else None
        self.blueprint_class = object()
        self.duplicated = []
        self.saved = []

        module = types.ModuleType("unreal")
        module.InputActionValueType = types.SimpleNamespace(BOOLEAN="Boolean")
        module.Key = FakeKey
        module.get_default_object = lambda blueprint_class: self.default_object
        module.EditorAssetLibrary = types.SimpleNamespace(
            does_asset_exist=self.does_asset_exist,
            duplicate_asset=self.duplicate_asset,
            load_asset=self.load_asset,
            load_blueprint_class=self.load_blueprint_class,
            save_asset=self.save_asset,
        )
        self.module = module

    def does_asset_exist(self, path):
        return self.action_exists

    def duplicate_asset(self, source, destination):
        self.duplicated.append((source, destination))
        self.action_exists = True
        return self.action

    def load_asset(self, path):
        if path.endswith("IA_PM_ArgusCapture"):
            return self.action if self.action_exists else None
        if path.endswith("IM_PM_Simple_MappingContext"):
            return self.context
        return None

    def load_blueprint_class(self, path):
        return self.blueprint_class

    def save_asset(self, path):
        self.saved.append(path)
        return True


def import_asset_module(monkeypatch, action_exists, f9_mappings=1):
    fake = FakeUnreal(action_exists, f9_mappings)
    monkeypatch.setitem(sys.modules, "unreal", fake.module)
    module = importlib.import_module("scripts.citysample_argus_assets")
    return importlib.reload(module), fake


def test_install_assets_creates_action_maps_f9_sets_cdo_and_saves(monkeypatch):
    module, fake = import_asset_module(
        monkeypatch, action_exists=False, f9_mappings=1
    )

    result = module.install_assets()

    assert result == {
        "action": module.ACTION_PATH,
        "f9_mappings": 1,
        "capture_action_set": True,
    }
    assert fake.duplicated == [(module.SOURCE_ACTION_PATH, module.ACTION_PATH)]
    assert fake.action.value_type == fake.module.InputActionValueType.BOOLEAN
    assert fake.context.unmapped == [(fake.action, "F9")]
    assert fake.context.mapped == [(fake.action, "F9")]
    assert len(fake.context.current_mappings) == 1
    assert fake.default_object.capture_action is fake.action
    assert fake.saved == [
        module.ACTION_PATH,
        module.CONTEXT_PATH,
        module.BLUEPRINT_PATH,
    ]


def test_install_assets_is_idempotent(monkeypatch):
    module, fake = import_asset_module(monkeypatch, action_exists=True)

    module.install_assets()
    module.install_assets()

    assert fake.duplicated == []
    assert len(fake.context.current_mappings) == 1


def test_install_assets_removes_every_duplicate_f9_mapping(monkeypatch):
    module, fake = import_asset_module(
        monkeypatch, action_exists=True, f9_mappings=3
    )

    module.install_assets()

    assert fake.context.unmapped == [(fake.action, "F9")] * 3
    assert len(fake.context.current_mappings) == 1


def test_verify_assets_rejects_missing_f9_mapping(monkeypatch):
    module, fake = import_asset_module(monkeypatch, action_exists=True)
    fake.action.value_type = fake.module.InputActionValueType.BOOLEAN
    fake.context.current_mappings.clear()

    with pytest.raises(RuntimeError, match="exactly one F9 mapping"):
        module.verify_assets()


def test_main_verify_writes_success_result(monkeypatch, tmp_path, capsys):
    module, fake = import_asset_module(monkeypatch, action_exists=True)
    fake.action.value_type = fake.module.InputActionValueType.BOOLEAN
    result_path = tmp_path / "result.json"
    result_path.write_text("stale", encoding="utf-8")

    assert module.main(["verify", "--result", str(result_path)]) == 0

    assert json.loads(result_path.read_text(encoding="utf-8"))["ok"] is True
    assert capsys.readouterr().out == "ARGUS_CITYSAMPLE_ASSETS_VERIFY_OK\n"
