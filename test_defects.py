import os
import sys
import unittest

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from process_defects import load_rfr_mapping


class TestDefectProcessing(unittest.TestCase):

    def setUp(self):
        # Create dummy CSV files for testing
        self.detail_csv = "test_detail.csv"
        self.group_csv = "test_group.csv"
        
        # Mock Detail Data: rfr_id | test_class_id | test_item_set_section_id
        # 1001 -> Class 4 -> Section 50
        # 1002 -> Class 1 -> Section 50 (Should be ignored)
        detail_data = """rfr_id|test_class_id|test_item_set_section_id
1001|4|50
1002|1|50
1003|4|51
"""
        with open(self.detail_csv, "w") as f:
            f.write(detail_data)

        # Mock Group Data: test_item_id | test_class_id | item_name
        # Section 50 -> Class 4 -> Brakes
        # Section 51 -> Class 4 -> Road Wheels (Should be renamed to Wheels)
        group_data = """test_item_id|test_class_id|item_name
50|4|Brakes
50|1|Brakes
51|4|Road Wheels
"""
        with open(self.group_csv, "w") as f:
            f.write(group_data)

    def tearDown(self):
        # Clean up dummy files
        if os.path.exists(self.detail_csv):
            os.remove(self.detail_csv)
        if os.path.exists(self.group_csv):
            os.remove(self.group_csv)

    def test_load_rfr_mapping(self):
        # Test the mapping logic
        mapping = load_rfr_mapping(self.detail_csv, self.group_csv, "4")
        
        self.assertIsNotNone(mapping)
        
        # Check if 1001 maps to Brakes
        self.assertIn("1001", mapping)
        self.assertEqual(mapping["1001"], "Brakes")
        
        # Check if 1002 is ignored (Class 1)
        self.assertNotIn("1002", mapping)
        
        # Check if 1003 maps to Wheels (Renamed from Road Wheels)
        self.assertIn("1003", mapping)
        self.assertEqual(mapping["1003"], "Wheels")

    def test_missing_files(self):
        # Test error handling for missing files
        mapping = load_rfr_mapping("non_existent.csv", self.group_csv, "4")
        self.assertIsNone(mapping)

if __name__ == '__main__':
    unittest.main()
