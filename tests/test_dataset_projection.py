import numpy as np

from tools.validate_dataset_projection import box_hits


def test_ray_box_handles_parallel_axes_and_behind_camera():
    directions = np.array([[[1, 0, 0], [0, 1, 0], [-1, 0, 0], [1, 2, 0]]], dtype=float)
    intersect, forward = box_hits(np.array([0, 0, 0]), directions,
                                  np.array([1, -1, -1]), np.array([3, 1, 1]))
    assert (intersect & forward).tolist() == [[True, False, False, False]]


def test_ray_box_accepts_camera_inside_box():
    intersect, forward = box_hits(np.array([0, 0, 0]), np.array([[[0, 0, 1]]], dtype=float),
                                  np.array([-1, -1, -1]), np.array([1, 1, 1]))
    assert bool((intersect & forward)[0, 0])
