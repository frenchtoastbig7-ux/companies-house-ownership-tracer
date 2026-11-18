import unittest

from chtracer.client import CompaniesHouseClient
from chtracer.models import Termination, normalise_company_number
from chtracer.traverse import OwnershipTracer

from .support import FixtureTransport


def build(max_depth=5, include_ceased=False):
    transport = FixtureTransport("complex_structure.json")
    client = CompaniesHouseClient(transport, cache_dir=None)
    return transport, OwnershipTracer(client, max_depth=max_depth, include_ceased=include_ceased)


class TestNormalisation(unittest.TestCase):
    def test_pads_numeric_company_numbers(self):
        self.assertEqual(normalise_company_number("1234567"), "01234567")
        self.assertEqual(normalise_company_number(" 2 "), "00000002")

    def test_leaves_prefixed_numbers_alone(self):
        self.assertEqual(normalise_company_number("sc123456"), "SC123456")


class TestTraversal(unittest.TestCase):
    def test_walks_corporate_psc_to_uk_parent(self):
        _, tracer = build()
        trace = tracer.trace("00000001")
        self.assertIn("00000002", trace.entities)
        self.assertEqual(trace.entities["00000002"].name, "BETA HOLDINGS LIMITED")

    def test_resolves_individual_at_top_of_chain(self):
        _, tracer = build()
        trace = tracer.trace("00000001")
        people = [e for e in trace.entities.values() if e.kind.value == "individual"]
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].termination, Termination.RESOLVED)

    def test_foreign_parent_terminates_chain_and_warns(self):
        _, tracer = build()
        trace = tracer.trace("00000001")
        foreign = [
            e for e in trace.entities.values()
            if e.termination == Termination.FOREIGN_JURISDICTION
        ]
        self.assertEqual(len(foreign), 1)
        self.assertEqual(foreign[0].detail["country_registered"], "Luxembourg")
        self.assertTrue(any("leaves the UK register" in w for w in trace.warnings))

    def test_ceased_psc_excluded_by_default(self):
        _, tracer = build()
        trace = tracer.trace("00000001")
        # DELTA is a ceased PSC of BETA, so it should not be walked.
        self.assertNotIn("00000004", trace.entities)

    def test_ceased_psc_included_on_request(self):
        _, tracer = build(include_ceased=True)
        trace = tracer.trace("00000001")
        self.assertIn("00000004", trace.entities)


class TestCycles(unittest.TestCase):
    """ALPHA -> BETA -> DELTA -> ALPHA. Must terminate."""

    def test_circular_ownership_terminates(self):
        _, tracer = build(include_ceased=True)
        trace = tracer.trace("00000001")   # completes or the test hangs
        self.assertIn("00000004", trace.entities)

    def test_cycle_edge_is_recorded(self):
        _, tracer = build(include_ceased=True)
        trace = tracer.trace("00000001")
        pairs = {(e.child, e.parent) for e in trace.edges}
        self.assertIn(("00000004", "00000001"), pairs)

    def test_each_company_fetched_once(self):
        transport, tracer = build(include_ceased=True)
        tracer.trace("00000001")
        profile_calls = [p for p in transport.paths_called() if p.count("/") == 2]
        self.assertEqual(len(profile_calls), len(set(profile_calls)))


class TestDepthLimit(unittest.TestCase):
    def test_stops_at_depth_and_flags_it(self):
        _, tracer = build(max_depth=1)
        trace = tracer.trace("00000001")
        limited = [
            e for e in trace.entities.values()
            if e.termination == Termination.DEPTH_LIMIT
        ]
        self.assertEqual([e.id for e in limited], ["00000002"])
        self.assertTrue(any("depth limit" in w for w in trace.warnings))

    def test_depth_zero_expands_nothing(self):
        _, tracer = build(max_depth=0)
        trace = tracer.trace("00000001")
        self.assertEqual(len(trace.edges), 0)


class TestMissingData(unittest.TestCase):
    def test_no_psc_records_statement(self):
        _, tracer = build()
        trace = tracer.trace("00000005")
        entity = trace.entities["00000005"]
        self.assertEqual(entity.termination, Termination.NO_PSC_DECLARED)
        self.assertIn("psc_statements", entity.detail)

    def test_unknown_company_is_not_found_not_crash(self):
        _, tracer = build()
        trace = tracer.trace("99999999")
        self.assertEqual(trace.entities["99999999"].termination, Termination.NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
