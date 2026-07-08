import importlib
import sys
import types


class FakeActor:
    def __init__(self, label, name=None):
        self._label = label
        self._name = name or label

    def get_actor_label(self):
        return self._label

    def get_name(self):
        return self._name


class FakeWorld:
    def __init__(self, actors):
        self.actors = actors


class BrokenEditorActorSubsystem:
    def get_all_level_actors(self):
        raise RuntimeError("The Editor is currently in a play mode.")


def import_common_with_fake_unreal(monkeypatch, fake_unreal):
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    sys.modules.pop("common", None)
    return importlib.import_module("common")


def test_find_actor_by_label_falls_back_to_game_world_when_editor_actor_lookup_fails(monkeypatch):
    target = FakeActor("SC_RGB")
    world = FakeWorld([FakeActor("Other"), target])

    class FakeEditorLevelLibrary:
        @staticmethod
        def get_game_world():
            return world

        @staticmethod
        def get_editor_world():
            return None

    class FakeGameplayStatics:
        @staticmethod
        def get_all_actors_of_class(world_context, actor_class):
            return list(world_context.actors)

    fake_unreal = types.SimpleNamespace(
        Actor=object,
        EditorActorSubsystem=object,
        EditorLevelLibrary=FakeEditorLevelLibrary,
        GameplayStatics=FakeGameplayStatics,
        get_editor_subsystem=lambda cls: BrokenEditorActorSubsystem(),
    )

    common = import_common_with_fake_unreal(monkeypatch, fake_unreal)

    assert common.find_actor_by_label("SC_RGB") is target


def test_find_actor_by_label_searches_later_worlds_when_first_world_has_other_actors(monkeypatch):
    target = FakeActor("SC_RGB")
    game_world = FakeWorld([FakeActor("Traffic"), FakeActor("Pedestrian")])
    editor_world = FakeWorld([FakeActor("SC_MASK"), target])

    class FakeEditorLevelLibrary:
        @staticmethod
        def get_game_world():
            return game_world

        @staticmethod
        def get_editor_world():
            return editor_world

    class FakeGameplayStatics:
        @staticmethod
        def get_all_actors_of_class(world_context, actor_class):
            return list(world_context.actors)

    fake_unreal = types.SimpleNamespace(
        Actor=object,
        EditorActorSubsystem=object,
        EditorLevelLibrary=FakeEditorLevelLibrary,
        GameplayStatics=FakeGameplayStatics,
        get_editor_subsystem=lambda cls: BrokenEditorActorSubsystem(),
    )

    common = import_common_with_fake_unreal(monkeypatch, fake_unreal)

    assert common.find_actor_by_label("SC_RGB") is target


def test_get_all_level_actors_falls_back_to_game_world_when_editor_actor_lookup_fails(monkeypatch):
    actors = [FakeActor("SC_RGB"), FakeActor("SC_MASK")]
    world = FakeWorld(actors)

    class FakeEditorLevelLibrary:
        @staticmethod
        def get_game_world():
            return world

        @staticmethod
        def get_editor_world():
            return None

    class FakeGameplayStatics:
        @staticmethod
        def get_all_actors_of_class(world_context, actor_class):
            return list(world_context.actors)

    fake_unreal = types.SimpleNamespace(
        Actor=object,
        EditorActorSubsystem=object,
        EditorLevelLibrary=FakeEditorLevelLibrary,
        GameplayStatics=FakeGameplayStatics,
        get_editor_subsystem=lambda cls: BrokenEditorActorSubsystem(),
    )

    common = import_common_with_fake_unreal(monkeypatch, fake_unreal)

    assert common.get_all_level_actors() == actors


def test_load_asset_or_raise_uses_runtime_loader_before_editor_asset_library(monkeypatch):
    asset = object()

    class BrokenEditorAssetLibrary:
        @staticmethod
        def load_asset(asset_path):
            raise RuntimeError("The Editor is currently in a play mode.")

    fake_unreal = types.SimpleNamespace(
        EditorAssetLibrary=BrokenEditorAssetLibrary,
        load_asset=lambda asset_path: asset,
    )

    common = import_common_with_fake_unreal(monkeypatch, fake_unreal)

    assert common.load_asset_or_raise("/Game/Tools/Semantic/RT_RGB") is asset


def test_mark_actor_always_loaded_for_world_partition_disables_spatial_loading(monkeypatch):
    class FakeSpatialActor:
        def __init__(self):
            self.editor_properties = {}

        def set_editor_property(self, name, value):
            self.editor_properties[name] = value

    fake_unreal = types.SimpleNamespace()
    common = import_common_with_fake_unreal(monkeypatch, fake_unreal)
    actor = FakeSpatialActor()

    assert common.mark_actor_always_loaded_for_world_partition(actor) is True
    assert actor.editor_properties["is_spatially_loaded"] is False


def test_make_rotator_uses_named_pitch_yaw_roll(monkeypatch):
    class FakeRotator:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.pitch = kwargs.get("pitch")
            self.yaw = kwargs.get("yaw")
            self.roll = kwargs.get("roll")

    fake_unreal = types.SimpleNamespace(Rotator=FakeRotator)

    common = import_common_with_fake_unreal(monkeypatch, fake_unreal)
    rotator = common.make_rotator(-90, 15, 5)

    assert rotator.args == ()
    assert rotator.pitch == -90.0
    assert rotator.yaw == 15.0
    assert rotator.roll == 5.0
