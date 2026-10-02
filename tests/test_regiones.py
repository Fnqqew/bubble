"""Manchas y mediana sin scipy (capture/regions.py): el mismo resultado que scipy.ndimage, que ya no se instala."""

import numpy as np
import pytest

from bubble.capture import regions


def test_numbers_spots_in_reading_order_with_their_boxes():
    mask = np.array([[1, 1, 0, 0, 1],
                     [0, 1, 0, 1, 1],
                     [0, 0, 0, 0, 0],
                     [1, 0, 1, 1, 0],
                     [0, 1, 0, 1, 0]], dtype=bool)
    labels, boxes = regions.regions(mask)
    assert labels.tolist() == [[1, 1, 0, 0, 2],
                               [0, 1, 0, 2, 2],
                               [0, 0, 0, 0, 0],
                               [3, 0, 4, 4, 0],
                               [0, 5, 0, 4, 0]]
    assert boxes[3] == (slice(3, 5), slice(2, 4))
    diagonal, count = regions.label(mask, diagonal=True)
    assert count == 3 and diagonal[4, 1] == diagonal[3, 0] == diagonal[3, 2]  # en diagonal se tocan
    assert regions.regions(np.zeros((3, 4), bool))[1] == [] and regions.label(np.zeros((0, 0), bool))[1] == 0


def test_a_u_shape_that_joins_at_the_bottom_is_one_spot():
    mask = np.zeros((6, 7), bool)
    mask[:, 0] = mask[:, 6] = mask[5] = True
    mask[0:4, 3] = True  # cuelga del borde de arriba sin tocar nada: es otra mancha
    labels, count = regions.label(mask)
    assert count == 2 and labels[0, 0] == labels[0, 6] == 1 and labels[0, 3] == 2


def test_median_keeps_the_background_and_erases_thin_letters():
    channel = np.full((20, 30), 200, np.uint8)
    channel[10, 5:25] = 0  # una letra fina
    assert (regions.median_filter(channel, 7) == 200).all()


def test_same_result_as_scipy():
    ndimage = pytest.importorskip("scipy.ndimage")  # solo en la PC de desarrollo (extra «pruebas»)
    rng = np.random.default_rng(3)
    for _ in range(400):
        mask = rng.random(tuple(rng.integers(1, 40, size=2))) < rng.uniform(0.05, 0.95)
        for diagonal in (False, True):
            expected, count = ndimage.label(mask, structure=np.ones((3, 3)) if diagonal else None)
            labels, boxes = regions.regions(mask, diagonal)
            assert np.array_equal(labels, expected) and len(boxes) == count
            assert boxes == [tuple(box) for box in ndimage.find_objects(expected)]
        channel = rng.integers(0, 256, size=mask.shape, dtype=np.uint8)
        assert np.array_equal(regions.median_filter(channel, 7), ndimage.median_filter(channel, size=7))
