"""Explicitly labelled, static dataset environments for the editor backend."""
import unreal

from create_capture_test_level import CaptureLabBuilder


class DatasetSceneBuilder(CaptureLabBuilder):
    def __init__(self, classes, recipe):
        super().__init__()
        self.classes, self.recipe = classes, recipe

    def mesh(self, key, *args, **kwargs):
        actor = super().mesh(key, *args, **kwargs)
        semantic = kwargs.get("semantic", "prop")
        actor.tags = ["ArgusBase:" + semantic]
        component = actor.static_mesh_component
        component.set_render_custom_depth(True)
        component.set_custom_depth_stencil_value(self.classes[semantic])
        component.set_evaluate_world_position_offset(False)
        return actor

    def build(self):
        for name, color in {"Terrain": (0.10, 0.14, 0.09), "Grass": (0.07, 0.20, 0.04),
                            "Sidewalk": (0.36, 0.33, 0.28), "Building": (0.30, 0.27, 0.23),
                            "Roof": (0.09, 0.10, 0.11), "Curb": (0.48, 0.47, 0.43)}.items():
            self.material(name, color)
        for element in self.recipe["elements"]:
            values = dict(element)
            kind = values.pop("kind")
            if kind == "building":
                self.building(**values)
            elif kind == "mesh":
                self.mesh(**values)
            else:
                raise ValueError("Unknown environment element: " + kind)
        self.lighting(**self.recipe["lighting"])


def validate_camera(pose, margin_cm=30):
    """Conservative bounds clearance, independent of mesh collision settings.

    Tests all static actors and each realized PCG instance. Bounding boxes can
    reject empty space inside a concave mesh; they do not guarantee useful framing.
    """
    from argus_backends.ue.pcg import current_world
    world = current_world()
    p = [pose[k] for k in ("x", "y", "z")]
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor):
        centre, extent = actor.get_actor_bounds(False)
        if all(abs(p[i] - getattr(centre, a)) <= getattr(extent, a) + margin_cm
               for i, a in enumerate(("x", "y", "z"))):
            raise RuntimeError("Camera intersects static bounds: " + actor.get_actor_label())
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.PCGVolume):
        for component in actor.get_components_by_class(unreal.InstancedStaticMeshComponent):
            box = component.static_mesh.get_bounding_box()
            for index in range(component.get_instance_count()):
                t = component.get_instance_transform(index, world_space=True)
                # Inverse unit-quaternion rotation, then inverse scale.
                q = t.rotation
                u = [-q.x, -q.y, -q.z]
                v = [p[i] - getattr(t.translation, a) for i, a in enumerate(("x", "y", "z"))]
                cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
                cross2 = [u[1]*cross[2]-u[2]*cross[1], u[2]*cross[0]-u[0]*cross[2], u[0]*cross[1]-u[1]*cross[0]]
                local = [(v[i] + 2*q.w*cross[i] + 2*cross2[i]) / getattr(t.scale3d, a)
                         for i, a in enumerate(("x", "y", "z"))]
                if all(getattr(box.min, a) - margin_cm / abs(getattr(t.scale3d, a)) <= local[i]
                       <= getattr(box.max, a) + margin_cm / abs(getattr(t.scale3d, a))
                       for i, a in enumerate(("x", "y", "z"))):
                    raise RuntimeError("Camera intersects PCG instance bounds: " + component.get_path_name())
