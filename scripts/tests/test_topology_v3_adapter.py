import unittest
from unittest.mock import patch
from scripts import map_outage_topology_impacts_v3 as v3


class V3AdapterTests(unittest.TestCase):
    def test_six_labels_preserved_with_explicit_selection_rule_adapter(self):
        self.assertEqual(set(v3.CAUSE_ADAPTER), {'Weather','Tree contact','Equipment Failure','Supply','Animal','Other'})
        for cause, expected in v3.CAUSE_ADAPTER.items():
            event={'cause_category':cause,'outage_id':'SYN-v3-test'}
            with patch.object(v3,'original_assign_source',return_value={'ok':True}) as original:
                self.assertEqual(v3.assign_source(event,None,None,42),{'ok':True})
                self.assertEqual(original.call_args.args[0]['cause_category'],expected)
            self.assertEqual(event['cause_category'],cause)
        self.assertEqual(v3.CAUSE_ADAPTER['Supply'],'Supply Interruption')

    def test_default_keeps_natural_branch_without_upstream_enlargement(self):
        policy=v3.generate.__defaults__[-1]
        self.assertEqual(policy.max_upstream_hops,0)
        self.assertEqual(policy.min_transformers,1)
        self.assertEqual(policy.min_line_km,0)


if __name__=='__main__': unittest.main()
