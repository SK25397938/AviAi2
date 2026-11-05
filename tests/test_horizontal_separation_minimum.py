import unittest
from pathlib import Path

from app.config.separation import MIN_HORIZONTAL_SEPARATION_NM, satisfies_horizontal_separation
class HorizontalSeparationMinimumTests(unittest.TestCase):
    def test_all_active_arrival_paths_share_six_nm(self):
        self.assertEqual(MIN_HORIZONTAL_SEPARATION_NM, 6.0)
        root = Path(__file__).resolve().parents[1]
        engine = (root / "app/simulation/engine.py").read_text(encoding="utf-8")
        trajectory = (root / "app/intelligence/trajectory.py").read_text(encoding="utf-8")
        self.assertIn("MIN_MERGE_SEPARATION_NM = MIN_HORIZONTAL_SEPARATION_NM", engine)
        self.assertIn("MIN_ARRIVAL_SEPARATION_NM = MIN_HORIZONTAL_SEPARATION_NM", trajectory)

    def test_six_nm_boundary(self):
        self.assertFalse(satisfies_horizontal_separation(5.9))
        self.assertTrue(satisfies_horizontal_separation(6.0))

if __name__ == "__main__":
    unittest.main()
