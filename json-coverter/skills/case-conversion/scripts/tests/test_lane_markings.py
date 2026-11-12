import sys
import unittest
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from normalization import lane_boundary_markings  # noqa: E402


class LaneBoundaryMarkingsTest(unittest.TestCase):
    def setUp(self):
        self.roads = [{
            "road_id": "road_001",
            "lane_ids": ["lane_001", "lane_002"],
        }]

    def test_relational_between_and_side(self):
        lanes = [{"lane_id": "lane_001"}, {"lane_id": "lane_002"}]
        markings = [
            {"between": ["lane_001", "lane_002"], "type": "dashed_white"},
            {"side": "right", "type": "solid_white_edge"},
        ]

        result = lane_boundary_markings(self.roads, lanes, markings)

        self.assertEqual(result["lane_001"], {"left": "dashed", "right": "solid"})
        self.assertEqual(result["lane_002"], {"left": "solid", "right": "dashed"})

    def test_legacy_boundary_ids_and_marking_type(self):
        lanes = [
            {"lane_id": "lane_001", "left_boundary_id": "marking_001"},
            {"lane_id": "lane_002", "right_boundary_id": "marking_001"},
        ]
        markings = [{"marking_id": "marking_001", "marking_type": "broken_line"}]

        result = lane_boundary_markings(self.roads, lanes, markings)

        self.assertEqual(result["lane_001"]["left"], "dashed")
        self.assertEqual(result["lane_002"]["right"], "dashed")

    def test_legacy_location_without_boundary_ids(self):
        lanes = [{"lane_id": "lane_001"}, {"lane_id": "lane_002"}]
        markings = [{
            "marking_id": "marking_001",
            "location": "Between lane_001 and lane_002.",
            "type": "dashed_line",
        }]

        result = lane_boundary_markings(self.roads, lanes, markings)

        self.assertEqual(result["lane_001"]["left"], "dashed")
        self.assertEqual(result["lane_002"]["right"], "dashed")


if __name__ == "__main__":
    unittest.main()
