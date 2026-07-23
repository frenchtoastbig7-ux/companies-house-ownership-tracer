import csv
import tempfile
import unittest
from pathlib import Path

from chtracer.analyze import cross_reference_officers, normalise_person_name
from chtracer.client import CompaniesHouseClient
from chtracer.export import to_dot, write_csv, write_json
from chtracer.traverse import OwnershipTracer

from .support import FixtureTransport


def traced(include_ceased=True):
    t = FixtureTransport("complex_structure.json")
    client = CompaniesHouseClient(t, cache_dir=None)
    return client, OwnershipTracer(client, include_ceased=include_ceased).trace("00000001")


class TestNameNormalisation(unittest.TestCase):
    def test_collapses_common_format_variants(self):
        variants = ["SMITH, John Andrew", "Mr John Andrew Smith", "john andrew smith"]
        normed = {normalise_person_name(v) for v in variants}
        self.assertEqual(len(normed), 1)

    def test_strips_titles_and_honours(self):
        self.assertEqual(
            normalise_person_name("Dr Jane Doe OBE"), normalise_person_name("Doe, Jane")
        )

    def test_different_people_do_not_collapse(self):
        self.assertNotEqual(
            normalise_person_name("John Smith"), normalise_person_name("Jane Smith")
        )


class TestOfficerCrossReference(unittest.TestCase):
    def test_finds_officer_at_multiple_companies(self):
        client, trace = traced()
        links = cross_reference_officers(client, trace)
        names = {l.normalised for l in links}
        self.assertIn(normalise_person_name("Priya Patel"), names)

    def test_matches_across_name_format_differences(self):
        client, trace = traced()
        links = cross_reference_officers(client, trace)
        patel = next(l for l in links if "PATEL" in l.normalised)
        # 'Mrs Priya PATEL' at 00000001 and 'Patel, Priya' at 00000004
        self.assertEqual(patel.company_count, 2)

    def test_resigned_officers_excluded_by_default(self):
        client, trace = traced()
        links = cross_reference_officers(client, trace, min_companies=1)
        self.assertFalse(any("OSBORNE" in l.normalised for l in links))

    def test_confidence_flags_missing_dob(self):
        client, trace = traced()
        links = cross_reference_officers(client, trace)
        for l in links:
            expected = "high" if l.dob else "low"
            self.assertTrue(l.to_row()["confidence"].startswith(expected))


class TestExport(unittest.TestCase):
    def test_csv_has_readable_names_on_both_ends(self):
        _, trace = traced()
        with tempfile.TemporaryDirectory() as tmp:
            write_csv(trace, tmp)
            with (Path(tmp) / "relationships.csv").open() as fh:
                rows = list(csv.DictReader(fh))
        self.assertTrue(all(r["child_name"] for r in rows))
        self.assertTrue(any(r["parent_name"] == "BETA HOLDINGS LIMITED" for r in rows))

    def test_entities_csv_sorted_by_depth(self):
        _, trace = traced()
        with tempfile.TemporaryDirectory() as tmp:
            write_csv(trace, tmp)
            with (Path(tmp) / "entities.csv").open() as fh:
                rows = list(csv.DictReader(fh))
        depths = [int(r["depth"]) for r in rows]
        self.assertEqual(depths, sorted(depths))

    def test_json_round_trips(self):
        _, trace = traced()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            write_json(trace, p)
            import json

            data = json.loads(p.read_text())
        self.assertEqual(data["summary"]["entity_count"], len(trace.entities))
        self.assertIn("terminations", data["summary"])

    def test_dot_output_is_balanced_and_labelled(self):
        _, trace = traced()
        dot = to_dot(trace)
        self.assertEqual(dot.count("{"), dot.count("}"))
        self.assertIn("BETA HOLDINGS LIMITED", dot)
        self.assertIn("foreign", dot)


if __name__ == "__main__":
    unittest.main()
