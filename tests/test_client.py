import tempfile
import unittest

from chtracer.client import AuthError, CompaniesHouseClient, NotFound, RateLimiter

from .support import FixtureTransport, PagedTransport, ScriptedTransport


class TestCaching(unittest.TestCase):
    def test_second_call_served_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = FixtureTransport("complex_structure.json")
            c = CompaniesHouseClient(t, cache_dir=tmp)
            c.company("00000001")
            c.company("00000001")
            self.assertEqual(len(t.calls), 1)

    def test_cache_survives_a_new_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            t1 = FixtureTransport("complex_structure.json")
            CompaniesHouseClient(t1, cache_dir=tmp).company("00000001")
            t2 = FixtureTransport("complex_structure.json")
            CompaniesHouseClient(t2, cache_dir=tmp).company("00000001")
            self.assertEqual(len(t2.calls), 0)

    def test_no_cache_always_hits_transport(self):
        t = FixtureTransport("complex_structure.json")
        c = CompaniesHouseClient(t, cache_dir=None)
        c.company("00000001")
        c.company("00000001")
        self.assertEqual(len(t.calls), 2)


class TestPagination(unittest.TestCase):
    def test_collects_every_page(self):
        c = CompaniesHouseClient(PagedTransport(total=250), cache_dir=None)
        self.assertEqual(len(c.pscs("00000001")), 250)

    def test_exact_multiple_of_page_size(self):
        c = CompaniesHouseClient(PagedTransport(total=200), cache_dir=None)
        self.assertEqual(len(c.pscs("00000001")), 200)

    def test_empty_result(self):
        c = CompaniesHouseClient(PagedTransport(total=0), cache_dir=None)
        self.assertEqual(c.pscs("00000001"), [])


class TestErrorHandling(unittest.TestCase):
    def test_404_on_company_raises_not_found(self):
        c = CompaniesHouseClient(FixtureTransport({}), cache_dir=None)
        with self.assertRaises(NotFound):
            c.company("00000001")

    def test_404_on_psc_list_is_empty_not_error(self):
        c = CompaniesHouseClient(FixtureTransport({}), cache_dir=None)
        self.assertEqual(c.pscs("00000001"), [])

    def test_401_raises_auth_error(self):
        t = ScriptedTransport([(401, {}, {})])
        c = CompaniesHouseClient(t, cache_dir=None)
        with self.assertRaises(AuthError):
            c.company("00000001")

    def test_429_is_retried_after_backoff(self):
        slept = []
        t = ScriptedTransport([
            (429, {}, {"Retry-After": "7"}),
            (200, {"company_name": "OK"}, {}),
        ])
        c = CompaniesHouseClient(t, cache_dir=None, sleep=slept.append)
        self.assertEqual(c.company("00000001")["company_name"], "OK")
        self.assertEqual(slept, [7.0])

    def test_500_is_retried_with_exponential_backoff(self):
        slept = []
        t = ScriptedTransport([
            (503, {}, {}),
            (503, {}, {}),
            (200, {"company_name": "OK"}, {}),
        ])
        c = CompaniesHouseClient(t, cache_dir=None, sleep=slept.append)
        c.company("00000001")
        self.assertEqual(slept, [1, 2])


class TestRateLimiter(unittest.TestCase):
    def test_blocks_once_window_is_full(self):
        now = [0.0]
        slept = []

        def sleep(s):
            slept.append(s)
            now[0] += s

        limiter = RateLimiter(max_requests=3, window=10, sleep=sleep, clock=lambda: now[0])
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(slept, [])
        limiter.acquire()
        self.assertEqual(len(slept), 1)

    def test_window_expiry_frees_capacity(self):
        now = [0.0]
        slept = []
        limiter = RateLimiter(
            max_requests=2, window=10, sleep=slept.append, clock=lambda: now[0]
        )
        limiter.acquire()
        limiter.acquire()
        now[0] = 11.0
        limiter.acquire()
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
