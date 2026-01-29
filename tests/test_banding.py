import unittest
import pandas as pd
import sys
import os

# Add parent directory to path to import utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_age_band, get_mileage_band

class TestBanding(unittest.TestCase):

    def test_get_age_band(self):
        self.assertEqual(get_age_band(pd.NA), 'Unknown')
        self.assertEqual(get_age_band(float('nan')), 'Unknown')
        self.assertEqual(get_age_band(0), '0-3')
        self.assertEqual(get_age_band(2.9), '0-3')
        self.assertEqual(get_age_band(3), '3-5')
        self.assertEqual(get_age_band(11), '10-15')
        self.assertEqual(get_age_band(15.9), '10-15')
        self.assertEqual(get_age_band(16), '15+')
        self.assertEqual(get_age_band(25), '15+')

    def test_get_mileage_band(self):
        self.assertEqual(get_mileage_band(pd.NA), 'Unknown')
        self.assertEqual(get_mileage_band(float('nan')), 'Unknown')
        self.assertEqual(get_mileage_band(-1), 'Unknown')
        self.assertEqual(get_mileage_band(0), '0-30k')
        self.assertEqual(get_mileage_band(29999), '0-30k')
        self.assertEqual(get_mileage_band(30000), '30k-60k')
        self.assertEqual(get_mileage_band(59999), '30k-60k')
        self.assertEqual(get_mileage_band(60000), '60k-100k')
        self.assertEqual(get_mileage_band(99999), '60k-100k')
        self.assertEqual(get_mileage_band(100000), '100k+')
        self.assertEqual(get_mileage_band(200000), '100k+')

if __name__ == '__main__':
    unittest.main()
