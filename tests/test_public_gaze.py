"""Geometry, split integrity, and fail-closed semantics for public gaze."""
import math
from types import SimpleNamespace
import unittest

import numpy as np

from shared.public_gaze import (
    PublicGazeEstimator, angles, angular_error, face_crop, mpii_direction, normalize,
)
from training.public_gaze_prepare import validate_splits


class GeometryTests(unittest.TestCase):
    def test_mpii_camera_ray_is_front_even_when_off_axis(self):
        self.assertTrue(np.allclose(mpii_direction([100,50,500],[0,0,0]), [0,0,-1]))

    def test_mpii_camera_axes_match_gaze360(self):
        left = mpii_direction([0,0,500],[-100,0,0])
        down = mpii_direction([0,0,500],[0,100,0])
        self.assertGreater(angles(left)[0], 0)
        self.assertLess(angles(down)[1], 0)
        self.assertAlmostEqual(angular_error([0,0,-1],left), math.degrees(math.atan(.2)))

    def test_angular_error_not_component_mse(self):
        self.assertAlmostEqual(angular_error([1,0,0],[0,0,-10]),90)
        self.assertAlmostEqual(angular_error([0,0,-1],[0,0,1]),180)
        for vector in ([0,0,0], [float("nan"),0,1], [1,2]):
            with self.assertRaises(ValueError):
                normalize(vector)

    def test_crop_rgb_contract_and_missing_landmarks(self):
        image = np.zeros((100,100,3),np.uint8)
        image[:,:,2] = 255
        points = [SimpleNamespace(x=.2 if i%2 else .8,y=.2 if i%3 else .8) for i in range(478)]
        crop = face_crop(image,points)
        self.assertEqual(crop.shape,(224,224,3))
        self.assertTrue((crop[:,:,0] == 255).all())
        self.assertTrue((crop[:,:,2] == 0).all())
        self.assertIsNone(face_crop(image,points[:3]))


class SplitTests(unittest.TestCase):
    def row(self, split, group, source):
        return dict(dataset="gaze360",split=split,group=group,source=source)

    def test_recording_leakage_fails_even_with_distinct_frames(self):
        with self.assertRaisesRegex(ValueError,"SPLIT_LEAKAGE"):
            validate_splits([self.row("train","rec1","frame1"),self.row("test","rec1","frame2")])

    def test_duplicate_full_faces_fail(self):
        with self.assertRaisesRegex(ValueError,"DUPLICATE_SAMPLE"):
            validate_splits([self.row("train","rec1","frame1")]*2)

    def test_disjoint_subjects_pass(self):
        result = validate_splits([self.row("train","p00","a"),self.row("val","p11","b"),self.row("test","p13","c")])
        self.assertEqual(result["group_overlap"],0)


class RuntimeTests(unittest.TestCase):
    def model(self):
        instance = PublicGazeEstimator.__new__(PublicGazeEstimator)
        instance.np = np
        instance.reference = None
        instance.reference_error = None
        instance.estimate = lambda *unused: self.observation(0,0,5)
        return instance

    def observation(self, yaw, pitch, error):
        y,p = math.radians(yaw),math.radians(pitch)
        return dict(vector=[math.sin(y)*math.cos(p),math.sin(p),-math.cos(y)*math.cos(p)],
                    yaw_degrees=yaw,pitch_degrees=pitch,error90_degrees=error,source="public_gaze_model")

    def test_missing_reference_is_unknown(self):
        self.assertEqual(self.model().observe(None,None,[0]*33)["direction"],"UNKNOWN")

    def test_neutral_requires_explicit_stable_samples(self):
        model = self.model()
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT"):
            model.set_reference([self.observation(0,0,5)]*14)
        with self.assertRaisesRegex(ValueError,"UNSTABLE"):
            model.set_reference([self.observation(-30 if i%2 else 30,0,5) for i in range(20)])
        model.set_reference([self.observation(0,0,5)]*15)
        result = model.observe(None,None,[0]*33)
        self.assertEqual(result["direction"],"CENTER")
        self.assertIsNone(result["offscreen_probability"])

    def test_blink_uncertainty_and_mesh_gate(self):
        model = self.model()
        model.set_reference([self.observation(0,0,5)]*15)
        model.estimate = lambda *unused: self.observation(45,0,25)
        self.assertEqual(model.observe(None,None,[0]*33)["direction"],"UNKNOWN")
        model.estimate = lambda *unused: self.observation(45,0,5)
        self.assertEqual(model.observe(None,None,[0]*33)["direction"],"LEFT")
        blink = [0]*33
        blink[29] = .9
        self.assertEqual(model.observe(None,None,blink)["direction"],"UNKNOWN")
        self.assertEqual(model.observe(None,None,None)["direction"],"UNKNOWN")

    def test_reference_and_measurement_errors_both_count(self):
        model = self.model()
        model.set_reference([self.observation(0,0,18)]*15)
        model.estimate = lambda *unused: self.observation(25,0,18)
        self.assertEqual(model.observe(None,None,[0]*33)["direction"],"UNKNOWN")


if __name__ == "__main__":
    unittest.main()
