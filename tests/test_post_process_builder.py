import importlib
import sys
import types


class FakeMaterial:
    pass


class FakeEditorAssetLibrary:
    existing_material = FakeMaterial()
    deleted_paths = []

    @classmethod
    def load_asset(cls, path):
        if path == "/Game/Tools/Semantic/M_PP_SemanticMask_Auto":
            return cls.existing_material
        return None

    @classmethod
    def delete_asset(cls, path):
        cls.deleted_paths.append(path)
        raise AssertionError("existing semantic material should be reused, not deleted")


class FakeAssetTools:
    def create_asset(self, *args, **kwargs):
        raise AssertionError("existing semantic material should be reused, not recreated")


def import_post_process(monkeypatch):
    fake_unreal = types.SimpleNamespace(
        AssetToolsHelpers=types.SimpleNamespace(
            get_asset_tools=lambda: FakeAssetTools(),
        ),
        EditorAssetLibrary=FakeEditorAssetLibrary,
        Material=object,
        MaterialFactoryNew=lambda: object(),
        log=lambda message: None,
    )
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in ("common", "argus_components.post_process"):
        sys.modules.pop(module_name, None)
    return importlib.import_module("argus_components.post_process")


def test_load_or_create_material_reuses_existing_asset_without_delete_or_create(monkeypatch):
    module = import_post_process(monkeypatch)
    FakeEditorAssetLibrary.deleted_paths = []

    builder = module.SemanticPostProcessBuilder()

    material = builder.load_or_create_material(
        "/Game/Tools/Semantic",
        "M_PP_SemanticMask_Auto",
    )

    assert material is FakeEditorAssetLibrary.existing_material
    assert FakeEditorAssetLibrary.deleted_paths == []
