import unittest
import pandas as pd

from v182.backtest.pit_source_registry_v15 import (
    PITSourceRegistryError, readiness, validate_source_registry,
)


class TestPITV15SourceRegistry(unittest.TestCase):
    def base(self):
        return pd.DataFrame([
            {'source':'P1_FACTSET','provider':'FACTSET','timestamp_field':'knowledge_date','publication_field':'publication_date','granularity':'daily','observed_lag_definition':'signal minus knowledge','pea_coverage_definition':'covered trades / total','licence':'documented','cost':'documented','point_in_time_guarantee':True,'active':True},
            {'source':'P2_EPS','provider':'EPS_PROVIDER','timestamp_field':'knowledge_date','publication_field':'publication_date','granularity':'weekly','observed_lag_definition':'signal minus knowledge','pea_coverage_definition':'covered trades / total','licence':'documented','cost':'documented','point_in_time_guarantee':True,'active':True},
            {'source':'P3_MODEL','provider':'INTERNAL','timestamp_field':'knowledge_date','publication_field':'publication_date','granularity':'monthly','observed_lag_definition':'signal minus knowledge','pea_coverage_definition':'model coverage','licence':'internal','cost':'internal','point_in_time_guarantee':True,'active':False},
        ])

    def test_primary_sources_documented(self):
        r=readiness(self.base())
        self.assertTrue(r['primary_source_documentation_ready'])
        self.assertFalse(r['performance_backtest_authorized_by_this_module'])

    def test_active_source_without_pit_guarantee_fails(self):
        z=self.base(); z.loc[0,'point_in_time_guarantee']=False
        with self.assertRaises(PITSourceRegistryError):
            validate_source_registry(z)

    def test_active_source_missing_licence_fails(self):
        z=self.base(); z.loc[0,'licence']=''
        with self.assertRaises(PITSourceRegistryError):
            validate_source_registry(z)


if __name__=='__main__':
    unittest.main()
